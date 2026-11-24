package video

import (
	"testing"

	"github.com/pion/rtp"
)

func TestParseSPSDimensions1080pAnd1440p(t *testing.T) {
	// Real H.264 High Profile 1920x1080 SPS (with bottom crop=4 -> 1088-8=1080)
	sps1080 := []byte{
		0x67, 0x64, 0x00, 0x2a, 0xac, 0xd9, 0x40, 0x78,
		0x02, 0x27, 0xe5, 0xc0, 0x44, 0x00, 0x00, 0x03,
		0x00, 0x04, 0x00, 0x00, 0x03, 0x00, 0xf0, 0x3c,
		0x60, 0xc6, 0x58,
	}
	w, h, ok := ParseSPSDimensions(sps1080)
	if !ok || w != 1920 || h != 1080 {
		t.Fatalf("1080p SPS = (%d, %d, %v), want (1920, 1080, true)", w, h, ok)
	}

	// Real H.264 Baseline 1280x720 SPS
	sps720 := []byte{
		0x67, 0x42, 0xe0, 0x1f, 0xda, 0x01, 0x40, 0x16,
		0xec, 0x04, 0x40, 0x00, 0x00, 0x03, 0x00, 0x40,
		0x00, 0x00, 0x0f, 0x03, 0xc6, 0x0c, 0x65, 0x80,
	}
	w, h, ok = ParseSPSDimensions(sps720)
	if !ok || w != 1280 || h != 720 {
		t.Fatalf("720p SPS = (%d, %d, %v), want (1280, 720, true)", w, h, ok)
	}
}

func TestDepacketizerFUAAndLossDetection(t *testing.T) {
	d := NewDepacketizer(60)
	// FU-A fragmented IDR frame (nalType = 5, 2 packets)
	p1 := &rtp.Packet{
		Header:  rtp.Header{SequenceNumber: 100, Timestamp: 90000, Marker: false},
		Payload: []byte{0x7C, 0x85, 0xAA, 0xBB}, // FU-A start, IDR
	}
	p2 := &rtp.Packet{
		Header:  rtp.Header{SequenceNumber: 101, Timestamp: 90000, Marker: true},
		Payload: []byte{0x7C, 0x45, 0xCC, 0xDD}, // FU-A end, IDR
	}
	if f := d.Push(p1); f != nil {
		t.Fatalf("expected nil before marker, got frame")
	}
	f := d.Push(p2)
	if f == nil || !f.Keyframe || f.FramesLost != 0 || f.Corrupted {
		t.Fatalf("unexpected IDR frame: %+v", f)
	}

	// Next frame after a 2-frame RTP timestamp gap (4500 ticks at 60 fps = 2 lost frames)
	p3 := &rtp.Packet{
		Header:  rtp.Header{SequenceNumber: 105, Timestamp: 94500, Marker: true},
		Payload: []byte{0x41, 0x11, 0x22},
	}
	f2 := d.Push(p3)
	if f2 == nil || f2.Keyframe || f2.FramesLost != 2 || !f2.Corrupted {
		t.Fatalf("unexpected post-gap frame: %+v", f2)
	}
}
