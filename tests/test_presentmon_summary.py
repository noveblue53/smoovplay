"""The console's --v1_metrics marks discarded frames even if the timing fields contain zero."""
from scripts.presentmon_summary import summarise


def test_xbox_v1_selects_presenter_and_counts_discarded_frames(tmp_path):
    path = tmp_path / "stock.csv"
    path.write_text(
        "Application,ProcessID,SwapChainAddress,Runtime,Dropped,msUntilDisplayed,msBetweenDisplayChange,msBetweenPresents\n"
        "XboxPcApp.exe,12,0xa,DXGI,0,4,16,16\nXboxPcApp.exe,12,0xa,DXGI,1,0,0,16\n"
        "XboxPcApp.exe,12,0xa,DXGI,0,8,32,16\nXboxPcApp.exe,45,0xa,DXGI,0,999,999,999\n"
        "python.exe,99,0xa,DXGI,0,18,17,16\n", encoding="utf-8-sig")
    lines = "\n".join(summarise(path, "xboxpcapp.exe"))
    assert "PID 12, swap chain 0xa; 3 presents" in lines
    assert "dropped (never shown): 1 (33.3%)" in lines
    assert "Present call until displayed: median 6.0 ms, p95 7.8 ms" in lines
    assert "between display changes: median 24.00 ms" in lines
    assert "PID 99" in "\n".join(summarise(path))


def test_v2_uses_displayed_time_and_distinguishes_cpu_latency(tmp_path):
    path = tmp_path / "rife.csv"
    path.write_text("Application,DisplayLatency,DisplayedTime\npython.exe,20,16\npython.exe,NA,NA\n"
                    "python.exe,30,20\n", encoding="utf-8")
    lines = "\n".join(summarise(path))
    assert "CPU frame start until displayed: median 25.0 ms" in lines
    assert "between display changes: median 18.00 ms" in lines
    assert "dropped (never shown): 1 (33.3%)" in lines
