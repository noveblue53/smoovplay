package frametap

import (
	"bytes"
	"encoding/binary"
	"testing"
)

func TestFrameTapHeaderAndSlotLayout(t *testing.T) {
	w, err := Open("test-tap")
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	defer w.Close()

	feats := FeatureAudioDelay | FeatureAudioTap
	w.Begin(CodecH264, 2560, 1440, 60, feats)

	if got := binary.LittleEndian.Uint32(w.mem[0:4]); got != Magic {
		t.Fatalf("magic = 0x%x, want 0x%x", got, Magic)
	}
	if got := binary.LittleEndian.Uint32(w.mem[4:8]); got != Version {
		t.Fatalf("version = %d, want %d", got, Version)
	}
	if got := binary.LittleEndian.Uint32(w.mem[8:12]); got != SlotCount {
		t.Fatalf("slot_count = %d, want %d", got, SlotCount)
	}
	if got := binary.LittleEndian.Uint32(w.mem[12:16]); got != SlotSize {
		t.Fatalf("slot_size = %d, want %d", got, SlotSize)
	}
	if got := binary.LittleEndian.Uint32(w.mem[20:24]); got != 2560 {
		t.Fatalf("width = %d, want 2560", got)
	}
	if got := binary.LittleEndian.Uint32(w.mem[24:28]); got != 1440 {
		t.Fatalf("height = %d, want 1440", got)
	}
	if got := binary.LittleEndian.Uint32(w.mem[64:68]); got != feats {
		t.Fatalf("features = %d, want %d", got, feats)
	}

	payload := []byte{0x00, 0x00, 0x00, 0x01, 0x65, 0xAA, 0xBB, 0xCC}
	w.Push(payload, 2, true)

	if got := binary.LittleEndian.Uint64(w.mem[32:40]); got != 1 {
		t.Fatalf("write_seq = %d, want 1", got)
	}
	off := HeaderSize
	if got := binary.LittleEndian.Uint64(w.mem[off : off+8]); got != 0 {
		t.Fatalf("slot seq = %d, want 0", got)
	}
	if got := binary.LittleEndian.Uint32(w.mem[off+16 : off+20]); got != uint32(len(payload)) {
		t.Fatalf("slot size = %d, want %d", got, len(payload))
	}
	if got := int32(binary.LittleEndian.Uint32(w.mem[off+20 : off+24])); got != 2 {
		t.Fatalf("frames_lost = %d, want 2", got)
	}
	if got := binary.LittleEndian.Uint32(w.mem[off+24 : off+28]); got != 1 {
		t.Fatalf("recovered = %d, want 1", got)
	}
	if !bytes.Equal(w.mem[off+SlotHeader:off+SlotHeader+len(payload)], payload) {
		t.Fatalf("slot payload mismatch")
	}

	// IDR backchannel
	binary.LittleEndian.PutUint32(w.mem[40:44], 1)
	if !w.TakeIDRRequest() {
		t.Fatal("expected TakeIDRRequest() = true")
	}
	if w.TakeIDRRequest() {
		t.Fatal("expected second TakeIDRRequest() = false")
	}

	// Audio delay backchannel (requires fresh heartbeat)
	binary.LittleEndian.PutUint64(w.mem[56:64], queryQPC())
	binary.LittleEndian.PutUint32(w.mem[88:92], 42)
	if got := w.AudioDelayMs(); got != 42 {
		t.Fatalf("AudioDelayMs = %d, want 42", got)
	}

	// Companion audio ring
	w.BeginAudio(48000, 2)
	if got := binary.LittleEndian.Uint32(w.audioMem[0:4]); got != AudioMagic {
		t.Fatalf("audio magic = 0x%x, want 0x%x", got, AudioMagic)
	}
	pcm := []byte{1, 2, 3, 4, 5, 6, 7, 8}
	w.PushAudio(pcm, 48000, 2)
	if got := binary.LittleEndian.Uint64(w.audioMem[24:32]); got != 1 {
		t.Fatalf("audio write_seq = %d, want 1", got)
	}
	if !bytes.Equal(w.audioMem[AudioHeaderSize+AudioSlotHeader:AudioHeaderSize+AudioSlotHeader+len(pcm)], pcm) {
		t.Fatalf("audio payload mismatch")
	}
}
