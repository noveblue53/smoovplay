// Package session orchestrates a native zero-jitter-buffer Xbox WebRTC streaming session
// directly into SmoovPlay's FrameTap shared memory, with RTCP PLI + ControlChannel keyframe
// recovery, 125 Hz XInput gamepad forwarding, and pure-Go Opus audio with SmoovPlay delay sync.
package session

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"sync"
	"sync/atomic"
	"time"

	"github.com/google/uuid"
	"github.com/pion/interceptor"
	"github.com/pion/rtcp"
	"github.com/pion/webrtc/v4"

	"github.com/noveblue53/smoovplay/xbox_tap/internal/audio"
	"github.com/noveblue53/smoovplay/xbox_tap/internal/frametap"
	"github.com/noveblue53/smoovplay/xbox_tap/internal/gssv"
	"github.com/noveblue53/smoovplay/xbox_tap/internal/input"
	"github.com/noveblue53/smoovplay/xbox_tap/internal/video"
)

const (
	controlAccessKey       = "4BDB3609-C1F1-4195-9B37-FEFF45DA8B8E"
	messageHandshakeID     = "be0bfc6d-1e83-4c8a-90ed-fa8601c5a179"
	messageClientAppInstID = "c97d7ee0-73b2-4239-bf1d-9d805a338429"
)

// Config configures a native Xbox streaming session.
type Config struct {
	Kind           gssv.StreamKind // KindHome or KindCloud
	TargetID       string          // console serverId (home) or titleId (cloud)
	Width          int             // preferred width (default 1920)
	Height         int             // preferred height (default 1080)
	FPS            int             // preferred fps (default 60)
	MaxBitrateKbps int             // SDP bitrate cap (default 20000)
	TapName        string          // SmoovPlay shared-memory tap name (default Local\smoovplay-tap)
	EnableAudio    bool            // decode Opus & play via Windows waveOut + AudioTap
	StatsInterval  time.Duration   // live RTT/loss/fps log interval (0 disables)
	LPTToken       string          // xCloud Long Play Token (only required for KindCloud)
}

// Run starts the Xbox session, connects WebRTC, streams H.264 straight into FrameTap,
// and blocks until ctx is cancelled or the peer connection disconnects.
func Run(ctx context.Context, client *gssv.Client, cfg Config) error {
	if cfg.Width <= 0 {
		cfg.Width = 1920
	}
	if cfg.Height <= 0 {
		cfg.Height = 1080
	}
	if cfg.FPS <= 0 {
		cfg.FPS = 60
	}
	if cfg.MaxBitrateKbps <= 0 {
		cfg.MaxBitrateKbps = 20000
	}

	tap, err := frametap.Open(cfg.TapName)
	if err != nil {
		return fmt.Errorf("open frametap: %w", err)
	}
	defer tap.Close()

	var audioPlayer *audio.Player
	if cfg.EnableAudio {
		tap.BeginAudio(audio.SampleRate, audio.Channels)
		audioPlayer, err = audio.NewPlayer(nil, func(rawPCM []byte) {
			tap.PushAudio(rawPCM, audio.SampleRate, audio.Channels)
		})
		if err != nil {
			log.Printf("[xbox-tap] warning: audio output disabled (%v)", err)
		} else {
			defer audioPlayer.Close()
		}
	}

	log.Printf("[xbox-tap] starting %s session for %s (%dx%d @ %d fps)...", cfg.Kind, cfg.TargetID, cfg.Width, cfg.Height, cfg.FPS)
	startResp, err := client.StartSession(ctx, cfg.Kind, cfg.TargetID)
	if err != nil {
		return fmt.Errorf("start session: %w", err)
	}
	sessionPath := startResp.SessionPath
	defer func() {
		stopCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = client.StopSession(stopCtx, sessionPath)
	}()

	log.Printf("[xbox-tap] session %s created; waiting for ReadyToConnect...", sessionPath)
	state, err := client.WaitForState(ctx, sessionPath, []string{"ReadyToConnect", "Provisioned"}, time.Second)
	if err != nil {
		return fmt.Errorf("wait ready: %w", err)
	}
	if cfg.Kind == gssv.KindCloud && state == "ReadyToConnect" {
		if cfg.LPTToken == "" {
			return fmt.Errorf("xCloud session requires Long Play Token (LPT)")
		}
		log.Printf("[xbox-tap] sending xCloud MSAL auth...")
		if err := client.SendMSALAuth(ctx, sessionPath, cfg.LPTToken); err != nil {
			return fmt.Errorf("xCloud MSAL /connect: %w", err)
		}
		if _, err := client.WaitForState(ctx, sessionPath, []string{"Provisioned"}, time.Second); err != nil {
			return fmt.Errorf("wait provisioned: %w", err)
		}
	}

	pc, err := newPeerConnection()
	if err != nil {
		return err
	}
	defer pc.Close()

	// RecvOnly video & audio transceivers (matches Xbox WebRTC expectations).
	if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeAudio, webrtc.RTPTransceiverInit{
		Direction: webrtc.RTPTransceiverDirectionRecvonly,
	}); err != nil {
		return err
	}
	if _, err := pc.AddTransceiverFromKind(webrtc.RTPCodecTypeVideo, webrtc.RTPTransceiverInit{
		Direction: webrtc.RTPTransceiverDirectionRecvonly,
	}); err != nil {
		return err
	}

	// Create the 4 ordered Xbox DataChannels.
	ordered := true
	dcControl, err := pc.CreateDataChannel("control", &webrtc.DataChannelInit{Ordered: &ordered, Protocol: strPtr("controlV1")})
	if err != nil {
		return err
	}
	dcInput, err := pc.CreateDataChannel("input", &webrtc.DataChannelInit{Ordered: &ordered, Protocol: strPtr("1.0")})
	if err != nil {
		return err
	}
	dcMessage, err := pc.CreateDataChannel("message", &webrtc.DataChannelInit{Ordered: &ordered, Protocol: strPtr("messageV1")})
	if err != nil {
		return err
	}
	_, err = pc.CreateDataChannel("chat", &webrtc.DataChannelInit{Ordered: &ordered, Protocol: strPtr("chatV1")})
	if err != nil {
		return err
	}

	runCtx, cancelRun := context.WithCancel(ctx)
	defer cancelRun()

	var videoSSRC atomic.Uint32
	var videoFrames atomic.Uint64
	var videoLost atomic.Uint64
	var videoKeyframes atomic.Uint64
	var audioPackets atomic.Uint64

	requestKeyframe := func() {
		if ssrc := videoSSRC.Load(); ssrc != 0 {
			_ = pc.WriteRTCP([]rtcp.Packet{&rtcp.PictureLossIndication{MediaSSRC: ssrc}})
		}
		sendControlJSON(dcControl, map[string]any{
			"message":      "videoKeyframeRequested",
			"ifrRequested": true,
		})
	}

	// Control channel handshake
	dcControl.OnOpen(func() {
		log.Printf("[xbox-tap] control channel open")
		sendControlJSON(dcControl, map[string]any{
			"message":   "authorizationRequest",
			"accessKey": controlAccessKey,
		})
		sendControlJSON(dcControl, map[string]any{
			"message":      "gamepadChanged",
			"gamepadIndex": 0,
			"wasAdded":     true,
		})
		sendControlJSON(dcControl, map[string]any{
			"message":    "VideoPreference",
			"type":       "VideoPreference",
			"resolution": map[string]int{"width": cfg.Width, "height": cfg.Height},
			"dimensions": map[string]int{"width": cfg.Width, "height": cfg.Height},
			"maxFPS":     cfg.FPS,
		})
		requestKeyframe()
	})

	// Message channel handshake
	dcMessage.OnOpen(func() {
		log.Printf("[xbox-tap] message channel open")
		sendControlJSON(dcMessage, map[string]any{
			"type":    "Handshake",
			"version": "messageV1",
			"id":      messageHandshakeID,
			"cv":      "0",
		})
	})
	dcMessage.OnMessage(func(msg webrtc.DataChannelMessage) {
		var env map[string]any
		if json.Unmarshal(msg.Data, &env) != nil {
			return
		}
		if env["type"] == "HandshakeAck" {
			sendMessageHandshakeConfig(dcMessage, cfg.Width, cfg.Height)
		}
	})

	// Input channel: 250 Hz XInput polling (immediate on change, 125 Hz idle heartbeat) + rumble feedback + FEATURE_PRESS delay test
	rumble := newRumbleController(input.SetRumble)
	defer rumble.stopAll()

	dcInput.OnOpen(func() {
		log.Printf("[xbox-tap] input channel open (250 Hz XInput polling)")
		go runInputLoop(runCtx, dcInput, dcControl, tap)
	})
	dcInput.OnMessage(func(msg webrtc.DataChannelMessage) {
		if vib, ok := input.ParseVibration(msg.Data); ok {
			rumble.apply(vib)
		}
	})

	// Zero-jitter-buffer RTP track handlers
	pc.OnTrack(func(track *webrtc.TrackRemote, receiver *webrtc.RTPReceiver) {
		codecMime := track.Codec().MimeType
		log.Printf("[xbox-tap] remote track attached: %s (ssrc=%d)", codecMime, track.SSRC())

		if track.Kind() == webrtc.RTPCodecTypeVideo {
			videoSSRC.Store(uint32(track.SSRC()))
			go func() {
				depacketizer := video.NewDepacketizer(cfg.FPS)
				started := false
				curW, curH := cfg.Width, cfg.Height
				feats := frametap.FeaturePress
				if audioPlayer != nil {
					feats |= frametap.FeatureAudioDelay | frametap.FeatureAudioTap
				}
				for {
					pkt, _, err := track.ReadRTP()
					if err != nil {
						if err != io.EOF && runCtx.Err() == nil {
							log.Printf("[xbox-tap] video track ended: %v", err)
						}
						return
					}
					frame := depacketizer.Push(pkt)
					if frame == nil {
						continue
					}
					if !started {
						if !frame.Keyframe {
							continue // wait for first IDR so NVDEC starts cleanly
						}
						if frame.Width > 0 && frame.Height > 0 {
							curW, curH = frame.Width, frame.Height
						}
						tap.Begin(frametap.CodecH264, uint32(curW), uint32(curH), uint32(cfg.FPS), feats)
						started = true
						log.Printf("[xbox-tap] stream live: H.264 %dx%d @ %d fps -> %s (0 ms jitter buffer)", curW, curH, cfg.FPS, cfg.TapName)
					} else if frame.Keyframe && frame.Width > 0 && frame.Height > 0 && (frame.Width != curW || frame.Height != curH) {
						log.Printf("[xbox-tap] resolution changed: %dx%d -> %dx%d", curW, curH, frame.Width, frame.Height)
						curW, curH = frame.Width, frame.Height
						tap.Begin(frametap.CodecH264, uint32(curW), uint32(curH), uint32(cfg.FPS), feats)
					}
					recovered := frame.Keyframe && (frame.FramesLost > 0 || frame.Corrupted)
					tap.Push(frame.Data, frame.FramesLost, recovered)
					videoFrames.Add(1)
					if frame.FramesLost > 0 {
						videoLost.Add(uint64(frame.FramesLost))
					}
					if frame.Keyframe {
						videoKeyframes.Add(1)
					}
				}
			}()
		} else if track.Kind() == webrtc.RTPCodecTypeAudio && audioPlayer != nil {
			go func() {
				for {
					pkt, _, err := track.ReadRTP()
					if err != nil {
						return
					}
					if err := audioPlayer.PushOpus(pkt.Payload); err == nil {
						audioPackets.Add(1)
					}
				}
			}()
		}
	})

	// Gather local ICE candidates while negotiating SDP
	var candMu sync.Mutex
	var localCands []gssv.ICECandidate
	gatherDone := make(chan struct{})
	var gatherOnce sync.Once
	pc.OnICECandidate(func(c *webrtc.ICECandidate) {
		if c == nil {
			gatherOnce.Do(func() { close(gatherDone) })
			return
		}
		init := c.ToJSON()
		idx := 0
		if init.SDPMLineIndex != nil {
			idx = int(*init.SDPMLineIndex)
		}
		mid := ""
		if init.SDPMid != nil {
			mid = *init.SDPMid
		}
		ufrag := ""
		if init.UsernameFragment != nil {
			ufrag = *init.UsernameFragment
		}
		candMu.Lock()
		localCands = append(localCands, gssv.ICECandidate{
			Candidate:        init.Candidate,
			SDPMLineIndex:    idx,
			SDPMid:           mid,
			UsernameFragment: ufrag,
		})
		candMu.Unlock()
	})

	pc.OnConnectionStateChange(func(st webrtc.PeerConnectionState) {
		log.Printf("[xbox-tap] WebRTC state: %s", st.String())
		if st == webrtc.PeerConnectionStateFailed || st == webrtc.PeerConnectionStateClosed || st == webrtc.PeerConnectionStateDisconnected {
			cancelRun()
		}
	})

	// Create SDP offer, prioritize H.264 High Profile + bitrate cap
	offer, err := pc.CreateOffer(nil)
	if err != nil {
		return fmt.Errorf("create offer: %w", err)
	}
	offer.SDP = gssv.PrioritizeH264AndBitrate(offer.SDP, cfg.MaxBitrateKbps)
	if err := pc.SetLocalDescription(offer); err != nil {
		return fmt.Errorf("set local description: %w", err)
	}

	// Wait up to 3s for ICE gathering to collect local host/srflx candidates
	select {
	case <-gatherDone:
	case <-time.After(3 * time.Second):
	case <-runCtx.Done():
		return runCtx.Err()
	}

	log.Printf("[xbox-tap] exchanging SDP...")
	answerSDP, err := client.ExchangeSDP(runCtx, sessionPath, offer.SDP)
	if err != nil {
		return fmt.Errorf("exchange SDP: %w", err)
	}
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{
		Type: webrtc.SDPTypeAnswer,
		SDP:  answerSDP,
	}); err != nil {
		return fmt.Errorf("set remote description: %w", err)
	}

	candMu.Lock()
	candsCopy := append([]gssv.ICECandidate(nil), localCands...)
	candMu.Unlock()

	log.Printf("[xbox-tap] exchanging ICE (%d local candidates)...", len(candsCopy))
	remoteCands, err := client.ExchangeICE(runCtx, sessionPath, candsCopy)
	if err != nil {
		return fmt.Errorf("exchange ICE: %w", err)
	}
	for _, rc := range remoteCands {
		idx := uint16(rc.SDPMLineIndex)
		mid := rc.SDPMid
		_ = pc.AddICECandidate(webrtc.ICECandidateInit{
			Candidate:     rc.Candidate,
			SDPMLineIndex: &idx,
			SDPMid:        &mid,
		})
	}
	log.Printf("[xbox-tap] added %d remote ICE candidates; connecting...", len(remoteCands))

	// Background loop: SmoovPlay IDR backchannel, audio delay sync, GSSV keepalive, live stats
	go func() {
		backTick := time.NewTicker(4 * time.Millisecond)
		keepTick := time.NewTicker(30 * time.Second)
		defer backTick.Stop()
		defer keepTick.Stop()

		var statsTick *time.Ticker
		var statsC <-chan time.Time
		if cfg.StatsInterval > 0 {
			statsTick = time.NewTicker(cfg.StatsInterval)
			statsC = statsTick.C
			defer statsTick.Stop()
		}
		var lastFrames uint64
		var lastStatsAt = time.Now()
		var lastAudioDelay uint32 = 0xFFFFFFFF

		for {
			select {
			case <-runCtx.Done():
				return
			case <-backTick.C:
				if tap.TakeIDRRequest() {
					log.Printf("[xbox-tap] SmoovPlay requested keyframe -> sending RTCP PLI + control IDR")
					requestKeyframe()
				}
				if audioPlayer != nil {
					if delay := tap.AudioDelayMs(); delay != lastAudioDelay {
						lastAudioDelay = delay
						audioPlayer.SetDelayMs(delay)
					}
				}
			case <-keepTick.C:
				_ = client.Keepalive(runCtx, sessionPath)
			case now := <-statsC:
				f := videoFrames.Load()
				dt := now.Sub(lastStatsAt).Seconds()
				fps := 0.0
				if dt > 0 {
					fps = float64(f-lastFrames) / dt
				}
				lastFrames = f
				lastStatsAt = now
				rttMs := readPeerRTTMs(pc)
				if rttMs >= 0 {
					log.Printf("[xbox-tap] webrtc: rtt=%.1fms jb=0.0ms fps=%.0f frames=%d kf=%d lost=%d audio_pkts=%d",
						rttMs, fps, f, videoKeyframes.Load(), videoLost.Load(), audioPackets.Load())
				} else {
					log.Printf("[xbox-tap] webrtc: jb=0.0ms fps=%.0f frames=%d kf=%d lost=%d audio_pkts=%d",
						fps, f, videoKeyframes.Load(), videoLost.Load(), audioPackets.Load())
				}
			}
		}
	}()

	<-runCtx.Done()
	tap.End()
	return nil
}

func newPeerConnection() (*webrtc.PeerConnection, error) {
	m := &webrtc.MediaEngine{}
	if err := m.RegisterDefaultCodecs(); err != nil {
		return nil, err
	}
	ir := &interceptor.Registry{}
	if err := webrtc.RegisterDefaultInterceptors(m, ir); err != nil {
		return nil, err
	}
	var se webrtc.SettingEngine
	se.SetReceiveMTU(8192)
	api := webrtc.NewAPI(
		webrtc.WithMediaEngine(m),
		webrtc.WithInterceptorRegistry(ir),
		webrtc.WithSettingEngine(se),
	)
	return api.NewPeerConnection(webrtc.Configuration{
		BundlePolicy:  webrtc.BundlePolicyMaxBundle,
		RTCPMuxPolicy: webrtc.RTCPMuxPolicyRequire,
	})
}

// syntheticPress tracks an active FEATURE_PRESS hold (SmoovPlay Delay Test).
type syntheticPress struct {
	buttons uint16
	until   time.Time
}

func (s *syntheticPress) trigger(now time.Time, xboxButtons uint16, holdMs uint32) bool {
	if xboxButtons == 0 {
		return false
	}
	s.buttons |= xboxButtons
	until := now.Add(time.Duration(holdMs) * time.Millisecond)
	if until.After(s.until) {
		s.until = until
	}
	return true
}

// step advances expiration; returns (currentButtons, releaseJustHappened).
func (s *syntheticPress) step(now time.Time) (uint16, bool) {
	if s.buttons != 0 && !now.Before(s.until) {
		s.buttons = 0
		return 0, true
	}
	return s.buttons, false
}

const (
	inputPollInterval      = 4 * time.Millisecond // 250 Hz XInput + TakePressRequest polling
	inputHeartbeatInterval = 8 * time.Millisecond // 125 Hz idle heartbeat
)

// rumbleController serializes rumble updates per gamepad and ensures any new
// vibration packet cancels/supersedes a pending zeroing timer for that controller.
type rumbleController struct {
	mu     sync.Mutex
	setFn  func(index uint8, left, right float32)
	timers map[uint8]*time.Timer
	gen    map[uint8]uint64
	closed bool
}

func newRumbleController(setFn func(index uint8, left, right float32)) *rumbleController {
	return &rumbleController{
		setFn:  setFn,
		timers: make(map[uint8]*time.Timer),
		gen:    make(map[uint8]uint64),
	}
}

func (r *rumbleController) apply(vib input.VibrationReport) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed {
		return
	}
	idx := vib.GamepadIndex
	r.gen[idx]++
	gen := r.gen[idx]
	if t, ok := r.timers[idx]; ok {
		t.Stop()
		delete(r.timers, idx)
	}
	r.setFn(idx, vib.LeftMotor, vib.RightMotor)
	if vib.DurationMs > 0 {
		d := time.Duration(vib.DurationMs) * time.Millisecond
		r.timers[idx] = time.AfterFunc(d, func() {
			r.mu.Lock()
			defer r.mu.Unlock()
			if r.closed || r.gen[idx] != gen {
				return
			}
			delete(r.timers, idx)
			r.setFn(idx, 0, 0)
		})
	}
}

func (r *rumbleController) stopAll() {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.closed = true
	for idx, t := range r.timers {
		t.Stop()
		delete(r.timers, idx)
		r.setFn(idx, 0, 0)
	}
}

// inputDispatcher polls gamepads and synthetic press requests, dispatching a packet
// immediately on any controller state change and maintaining a 125 Hz heartbeat when idle.
type inputDispatcher struct {
	start        time.Time
	seq          uint32
	wasConnected [4]bool
	synth        syntheticPress
	lastSent     []input.GamepadState
	lastSentAt   time.Time

	pollGamepad      func(index uint8) input.GamepadState
	takePressRequest func() (buttons uint32, holdMs uint32)
	onPressSent      func()
	onGamepadChanged func(index int, connected bool)
	sendPacket       func(pkt []byte) error
}

func (d *inputDispatcher) step(now time.Time) bool {
	notifyPressSent := false
	if d.takePressRequest != nil {
		if chiakiBtns, holdMs := d.takePressRequest(); chiakiBtns != 0 {
			xboxBtns := input.MapChiakiButtons(chiakiBtns)
			if d.synth.trigger(now, xboxBtns, holdMs) {
				notifyPressSent = true
			}
		}
	}
	synthBtns, synthReleased := d.synth.step(now)

	var active []input.GamepadState
	for i := uint8(0); i < 4; i++ {
		st := d.pollGamepad(i)
		if i == 0 && (synthBtns != 0 || synthReleased) {
			// Ensure virtual gamepad 0 stays attached during a synthetic press/release.
			if !st.Connected {
				st.Connected = true
			}
			st.Buttons |= synthBtns
		}
		if st.Connected != d.wasConnected[i] {
			d.wasConnected[i] = st.Connected
			if d.onGamepadChanged != nil {
				d.onGamepadChanged(int(i), st.Connected)
			}
		}
		if st.Connected {
			active = append(active, st)
		}
	}
	if len(active) == 0 {
		d.lastSent = nil
		return false
	}

	changed := !gamepadStatesEqual(active, d.lastSent)
	heartbeatDue := d.lastSentAt.IsZero() || now.Sub(d.lastSentAt) >= inputHeartbeatInterval
	if !changed && !notifyPressSent && !synthReleased && !heartbeatDue {
		return false
	}

	tsMs := float64(now.Sub(d.start).Milliseconds())
	pkt := input.EncodeGamepadPacket(d.seq, tsMs, active)
	d.seq++
	if err := d.sendPacket(pkt); err == nil {
		d.lastSent = append(d.lastSent[:0], active...)
		d.lastSentAt = now
		if notifyPressSent && d.onPressSent != nil {
			d.onPressSent()
		}
		return true
	}
	return false
}

func gamepadStatesEqual(a, b []input.GamepadState) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}

func runInputLoop(ctx context.Context, dcInput, dcControl *webrtc.DataChannel, tap *frametap.Writer) {
	start := time.Now()
	var seq uint32 = 1
	// Send initial ClientMetadata packet once on open.
	_ = dcInput.Send(input.EncodeClientMetadata(seq, 0, 1))
	seq++

	ticker := time.NewTicker(inputPollInterval) // 250 Hz
	defer ticker.Stop()

	disp := &inputDispatcher{
		start:        start,
		seq:          seq,
		wasConnected: [4]bool{true, false, false, false},
		pollGamepad:  input.PollGamepad,
		onGamepadChanged: func(index int, connected bool) {
			sendControlJSON(dcControl, map[string]any{
				"message":      "gamepadChanged",
				"gamepadIndex": index,
				"wasAdded":     connected,
			})
		},
		sendPacket: dcInput.Send,
	}
	if tap != nil {
		disp.takePressRequest = tap.TakePressRequest
		disp.onPressSent = tap.PressSent
	}

	for {
		select {
		case <-ctx.Done():
			return
		case now := <-ticker.C:
			if dcInput.ReadyState() != webrtc.DataChannelStateOpen {
				continue
			}
			disp.step(now)
		}
	}
}

func sendControlJSON(dc *webrtc.DataChannel, v any) {
	if dc == nil || dc.ReadyState() != webrtc.DataChannelStateOpen {
		return
	}
	b, err := json.Marshal(v)
	if err != nil {
		return
	}
	_ = dc.SendText(string(b))
}

func sendMessageHandshakeConfig(dc *webrtc.DataChannel, width, height int) {
	sendMsg := func(target string, content any) {
		cb, err := json.Marshal(content)
		if err != nil {
			return
		}
		sendControlJSON(dc, map[string]any{
			"type":    "Message",
			"content": string(cb),
			"id":      uuid.NewString(),
			"target":  target,
			"cv":      "",
		})
	}
	sendMsg("/streaming/systemUi/configuration", map[string]any{
		"version":   []int{0, 2, 0},
		"systemUis": []any{},
	})
	sendMsg("/streaming/properties/clientappinstallidchanged", map[string]any{
		"clientAppInstallId": messageClientAppInstID,
	})
	sendMsg("/streaming/characteristics/orientationchanged", map[string]any{
		"orientation": 0,
	})
	sendMsg("/streaming/characteristics/touchinputenabledchanged", map[string]any{
		"touchInputEnabled": false,
	})
	sendMsg("/streaming/characteristics/clientdevicecapabilities", map[string]any{})
	sendMsg("/streaming/characteristics/dimensionschanged", map[string]any{
		"horizontal":               width,
		"vertical":                 height,
		"preferredWidth":           width,
		"preferredHeight":          height,
		"safeAreaLeft":             0,
		"safeAreaTop":              0,
		"safeAreaRight":            width,
		"safeAreaBottom":           height,
		"supportsCustomResolution": true,
	})
}

func readPeerRTTMs(pc *webrtc.PeerConnection) float64 {
	stats := pc.GetStats()
	for _, s := range stats {
		if cp, ok := s.(webrtc.ICECandidatePairStats); ok && cp.State == webrtc.StatsICECandidatePairStateSucceeded {
			if cp.CurrentRoundTripTime > 0 {
				return cp.CurrentRoundTripTime * 1000.0
			}
		}
	}
	return -1
}

func strPtr(s string) *string { return &s }
