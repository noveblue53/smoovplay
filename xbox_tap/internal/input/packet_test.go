package input

import (
	"encoding/binary"
	"testing"
)

func TestEncodeClientMetadataAndGamepadPacket(t *testing.T) {
	meta := EncodeClientMetadata(1, 10.0, 1)
	if len(meta) != 15 {
		t.Fatalf("clientMetadata len = %d, want 15", len(meta))
	}
	if got := binary.LittleEndian.Uint16(meta[0:2]); got != ReportClientMetadata {
		t.Fatalf("reportType = %d, want %d", got, ReportClientMetadata)
	}
	if meta[14] != 1 {
		t.Fatalf("maxTouchPoints = %d, want 1", meta[14])
	}

	pkt := EncodeGamepadPacket(7, 123.0, []GamepadState{{
		Index:        0,
		Connected:    true,
		Buttons:      BtnA | BtnRightShoulder,
		LeftThumbX:   16000,
		LeftThumbY:   -16000,
		RightThumbX:  -32000,
		RightThumbY:  32000,
		LeftTrigger:  32768,
		RightTrigger: 65535,
	}})
	if len(pkt) != 14+1+23 {
		t.Fatalf("gamepad packet len = %d, want 38", len(pkt))
	}
	if got := binary.LittleEndian.Uint16(pkt[0:2]); got != ReportGamepad {
		t.Fatalf("reportType = %d, want %d", got, ReportGamepad)
	}
	if got := binary.LittleEndian.Uint32(pkt[2:6]); got != 7 {
		t.Fatalf("seq = %d, want 7", got)
	}
	if pkt[14] != 1 {
		t.Fatalf("count = %d, want 1", pkt[14])
	}
	if got := binary.LittleEndian.Uint16(pkt[16:18]); got != (BtnA | BtnRightShoulder) {
		t.Fatalf("buttons = 0x%x, want 0x%x", got, BtnA|BtnRightShoulder)
	}
	if got := int16(binary.LittleEndian.Uint16(pkt[18:20])); got != 16000 {
		t.Fatalf("LX = %d, want 16000", got)
	}
	if got := binary.LittleEndian.Uint16(pkt[28:30]); got != 65535 {
		t.Fatalf("RT = %d, want 65535", got)
	}
	// PhysicalPhysicality LE=1, VirtualPhysicality BE=1
	if got := binary.LittleEndian.Uint32(pkt[30:34]); got != 1 {
		t.Fatalf("physical = %d, want 1", got)
	}
	if got := binary.BigEndian.Uint32(pkt[34:38]); got != 1 {
		t.Fatalf("virtual = %d, want 1", got)
	}
}

func TestParseVibrationAndServerMetadata(t *testing.T) {
	vibBytes := []byte{byte(ReportVibration), 0, 0, 0, 50, 100, 25, 0, 0xE8, 0x03, 0, 0, 1}
	v, ok := ParseVibration(vibBytes)
	if !ok || v.LeftMotor != 0.5 || v.RightMotor != 1.0 || v.DurationMs != 1000 {
		t.Fatalf("unexpected vibration: %+v, ok=%v", v, ok)
	}

	sm := make([]byte, 10)
	binary.LittleEndian.PutUint16(sm[0:2], ReportServerMetadata)
	binary.LittleEndian.PutUint32(sm[2:6], 1080)
	binary.LittleEndian.PutUint32(sm[6:10], 1920)
	w, h, ok := ParseServerMetadata(sm)
	if !ok || w != 1920 || h != 1080 {
		t.Fatalf("serverMetadata = (%d, %d, %v), want (1920, 1080, true)", w, h, ok)
	}
}
