import torch

from framegen.cadence import Action, CadenceScheduler, DuplicateDetector

I, C, P, H = Action.INTERP, Action.CURRENT, Action.PENDING, Action.HOLD


def run(pattern: str, cuts: str = "") -> list[Action]:
    """pattern: one char per tick, 'N' = new frame, 'd' = duplicate."""
    s = CadenceScheduler()
    return [s.step(ch == "N", i in {int(x) for x in cuts.split()} if cuts else False)
            for i, ch in enumerate(pattern)]


def test_steady_30fps_alternates_interp_and_real():
    assert run("NdNdNdNd") == [C, C, I, C, I, C, I, C]


def test_hitch_repeats_extra_duplicate():
    # A A A B B: the third A is simply shown again, then interpolation resumes.
    assert run("NddNdNd") == [C, C, C, I, C, I, C]


def test_60fps_section_passes_through_one_tick_late():
    # N N N N: after the first midpoint, each tick shows the previous new frame.
    assert run("NdNNNNdNd") == [C, C, I, P, P, P, C, I, C]


def test_xbox_menu_shows_every_original_and_drains_on_first_spare_beat():
    sched = CadenceScheduler(live_native_60=True)
    step = lambda pattern: [sched.step(c == "N") for c in pattern]
    assert step("NdNd") == [C, C, I, C]
    # A clean 30->60 transition has an original queued. Show every one in order
    # until the first repeat catches up, then show new native frames immediately.
    assert step("NNNNNNd") == [I, P, P, P, P, P, C]
    assert step("NNNNNN") == [C] * 6
    assert step("dNdNdN") == [C, C, C, I, C, I]


def test_xbox_native_60_start_has_no_interpolation_or_lookahead():
    sched = CadenceScheduler(live_native_60=True)
    assert all(sched.step(True) is C for _ in range(180))


def test_scene_cut_holds_instead_of_interpolating():
    assert run("NdNdNd", cuts="2") == [C, C, H, C, I, C]


def test_detector_ignores_exact_and_noisy_repeats_but_sees_small_motion():
    g = torch.Generator().manual_seed(0)
    base = torch.randint(0, 256, (1080, 1920, 3), dtype=torch.uint8, generator=g)
    det = DuplicateDetector(threshold=2.0)
    assert det.update(base).is_new  # first frame

    assert not det.update(base.clone()).is_new  # bit-exact repeat
    noisy = (base.int() + torch.randint(-3, 4, base.shape, generator=g)).clamp(0, 255).to(torch.uint8)
    assert not det.update(noisy).is_new  # +-3 per-pixel noise averages out in 16x16 blocks

    moved = noisy.clone()
    moved[500:540, 900:940] = 255 - moved[500:540, 900:940]  # one small object changes
    r = det.update(moved)
    assert r.is_new and r.mean_diff < 1.0  # global mean barely moves; block max catches it


def test_cut_detector_flags_spikes_not_sustained_fast_motion():
    from framegen.cadence import CutDetector

    d = CutDetector(threshold=25.0)
    # steady motion, a cut, back to normal, then sustained fast motion (camera whip pan)
    seq = [8, 9, 7, 60, 10, 9, 28, 33, 35, 34, 32]
    flags = [d.update(m) for m in seq]
    assert flags == [False, False, False, True, False, False, False, False, False, False, False]


def _dark_scene(seed: int) -> torch.Tensor:
    """A dark 1080p frame (brightness ~5-40) with its own layout of light and shadow."""
    g = torch.Generator().manual_seed(seed)
    blobs = torch.rand((1, 1, 9, 16), generator=g)
    layout = torch.nn.functional.interpolate(blobs, size=(1080, 1920), mode="bilinear", align_corners=False)
    return (5 + 35 * layout[0, 0]).unsqueeze(-1).expand(1080, 1920, 3).to(torch.uint8).contiguous()


def test_cut_between_dark_shots_is_caught_by_structure():
    from framegen.cadence import CutDetector

    a, b = _dark_scene(1), _dark_scene(2)
    det = DuplicateDetector()
    det.update(a)
    same = det.update(a.clone())
    cut = det.update(b)
    assert cut.mean_diff < 25  # too small for the plain-difference test on its own
    assert cut.cut.corr_dist > 0.75 > same.cut.corr_dist
    assert not CutDetector(use_structure=False).update(cut.mean_diff, cut.cut)
    assert CutDetector().update(cut.mean_diff, cut.cut)


def test_structure_needs_a_jump_and_a_real_change():
    from framegen.cadence import CutDetector, CutFeatures

    d = CutDetector()
    noise = CutFeatures(corr_dist=1.2, hist_dist=0.1, luma=3, contrast=0.5)  # near-black frame noise
    assert not d.update(2.0, noise)  # layout "changes" completely, but the picture barely changed
    d = CutDetector()
    moving = [CutFeatures(c, 0.1, 40, 20) for c in (0.9, 0.95, 0.85, 0.9)]  # a long fast turn
    assert [d.update(12.0, f) for f in moving] == [True, False, False, False]  # only the start can look like a cut


def test_flat_frames_are_alike_and_flat_to_detail_is_not():
    from framegen.cadence import cut_features

    black = torch.zeros((1, 3, 68, 120))
    detail = torch.rand((1, 3, 68, 120), generator=torch.Generator().manual_seed(3)) * 255
    assert cut_features(black, black.clone()).corr_dist == 0.0
    assert cut_features(black, detail).corr_dist == 1.0
