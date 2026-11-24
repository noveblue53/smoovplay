package session

import (
	"testing"
	"time"

	"github.com/noveblue53/smoovplay/xbox_tap/internal/input"
)

func TestSyntheticPressLifecycle(t *testing.T) {
	var s syntheticPress
	t0 := time.Unix(1000, 0)

	// Zero button mask is a no-op
	if s.trigger(t0, 0, 100) {
		t.Fatal("expected trigger(0) = false")
	}
	if btns, rel := s.step(t0); btns != 0 || rel {
		t.Fatalf("idle step = (0x%x, %v), want (0, false)", btns, rel)
	}

	// Trigger Menu for 100 ms
	if !s.trigger(t0, input.BtnMenu, 100) {
		t.Fatal("expected trigger(BtnMenu) = true")
	}
	if btns, rel := s.step(t0); btns != input.BtnMenu || rel {
		t.Fatalf("at t0: step = (0x%x, %v), want (BtnMenu, false)", btns, rel)
	}
	if btns, rel := s.step(t0.Add(50 * time.Millisecond)); btns != input.BtnMenu || rel {
		t.Fatalf("at t0+50ms: step = (0x%x, %v), want (BtnMenu, false)", btns, rel)
	}
	// At t0+100ms: expires and signals release once
	if btns, rel := s.step(t0.Add(100 * time.Millisecond)); btns != 0 || !rel {
		t.Fatalf("at t0+100ms: step = (0x%x, %v), want (0, true)", btns, rel)
	}
	// Subsequent tick: idle again
	if btns, rel := s.step(t0.Add(108 * time.Millisecond)); btns != 0 || rel {
		t.Fatalf("at t0+108ms: step = (0x%x, %v), want (0, false)", btns, rel)
	}
}
