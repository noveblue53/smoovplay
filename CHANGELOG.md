# Changelog

## 0.3.13

**Install**
- **Setup no longer uses or uninstalls the packages of your own Python 3.12**
  ([#53](https://github.com/smoovplay/smoovplay/issues/53)). SmoovPlay's Python also read the folder where
  `pip install --user` puts packages for a regular Python 3.12. So setup counted those as already installed, removed
  your own copies when it wanted other versions, and SmoovPlay came to depend on them. It now leaves that folder
  alone, at install and while you play, and the update puts whatever it skipped into SmoovPlay's own folder. Packages
  it already removed from your Python come back with `pip install --user`. Thanks to
  [@NewAiCoder](https://github.com/NewAiCoder) for the precise report and the fix!

**AMD and Intel graphics** (Experimental)
- **No more crash on the first frame with some AMD drivers**
  ([#59](https://github.com/smoovplay/smoovplay/issues/59)). When the window was smaller than the stream, the
  driver of an RX 6750 XT refused the call SmoovPlay draws the picture with, and the window closed. SmoovPlay now
  draws it another way when that happens.

## 0.3.12

**App window** (Experimental)
- **The Xbox app in full screen is no longer just missing from the list**
  ([#49](https://github.com/smoovplay/smoovplay/issues/49), [#43](https://github.com/smoovplay/smoovplay/issues/43)). In
  full screen, Windows moves a Store app's window out of the list of windows SmoovPlay reads, so the Xbox app wasn't
  offered, and nothing said why. The launcher now says it's in full screen and how to leave it (its full screen button,
  or Shift+Win+Enter), and so does Play. In a window, it's found as before.

**Smoothness**
- **Auto motion detail picks Half on cards too slow for Full**
  ([#46](https://github.com/smoovplay/smoovplay/issues/46)). Auto used Full at 60 and 90 fps on any card, and on an RTX
  2080 Ti Full doesn't keep up even at 60. Auto-detect now keeps its timings, and Auto goes by them: Half wherever Full
  won't keep up at the frame rate you picked. On an existing install, run Auto-detect once (at the top of the Settings
  page) to save them.

**AMD and Intel graphics** (Experimental)
- **The delay test (L) no longer slows the picture** ([#48](https://github.com/smoovplay/smoovplay/issues/48)). It read
  every pixel of each frame on the CPU, which cost about 5 ms a frame without an NVIDIA card and dropped the frame rate
  while it ran. It now reads every 4th pixel, with the same result. Thanks to
  [@DEATHRIDER28310](https://github.com/DEATHRIDER28310) for measuring it!

**Video window**
- **F goes full screen on the monitor the window is on** ([#30](https://github.com/smoovplay/smoovplay/issues/30)). It
  always filled the main monitor, so a window moved to a second screen jumped back to the first.

## 0.3.11

**App window** (Experimental)
- **No more "Python is not responding" and a black picture after a few seconds**
  ([#33](https://github.com/smoovplay/smoovplay/issues/33), [#34](https://github.com/smoovplay/smoovplay/issues/34)).
  SmoovPlay checked for its shortcuts (Ctrl+Alt+M and the rest) in a way that made Windows treat the video window as
  not responding after 5 seconds. Windows then put a frozen copy of the window on screen, which window capture read
  back as a black picture, and offered to close SmoovPlay. Thanks to
  [@KazimeirzOdnowiciel](https://github.com/KazimeirzOdnowiciel) ([#39](https://github.com/smoovplay/smoovplay/pull/39))!

**Capture cards** (Experimental)
- **A damaged frame from the card no longer stops capture** ([#38](https://github.com/smoovplay/smoovplay/issues/38),
  [#41](https://github.com/smoovplay/smoovplay/issues/41)). Cards and sticks that send compressed video (MJPEG), as
  many USB ones do at 1080p 60, sometimes send a frame that can't be read, often right at the start. SmoovPlay
  stopped capturing at the first one, so the video window closed straight away or never showed a picture. Such frames
  are now skipped, and the log counts them. If none of the card's frames can be read for 5 seconds, capture stops
  with a message that says so. With an NVIDIA card, a damaged frame also no longer moves the decoding from the
  graphics card to the CPU.

**AMD and Intel graphics** (Experimental)
- **Less CPU use while playing** ([#40](https://github.com/smoovplay/smoovplay/issues/40)). Without an NVIDIA card,
  part of SmoovPlay's work runs on the CPU every frame, and the threads doing it kept most of the CPU's cores busy
  between frames while they waited for the next one. They now wait asleep.

**Diagnostics**
- **The results no longer say the PC is falling behind at 60 fps when it keeps up**
  ([#20](https://github.com/smoovplay/smoovplay/issues/20)). At 60 fps they counted frames waiting for the screen's
  next refresh as the PC falling behind, but with VSync every frame waits for it, so a fast PC looked slow. The
  verdict now goes by how long each frame took to make, and a new line says how many took longer than a 60 fps frame
  lasts. At 90 and 120 fps it still goes by how many of the extra frames were shown.
- **A freeze leaves more in the log.** The watchdog notes a stuck video loop after 4 seconds instead of 8, before
  Windows would call the window not responding, and if Python freezes as a whole, a timer of its own writes where
  every thread was.

## 0.3.10

**Capture cards** (Experimental)
- **Game frame rate: 30 fps no longer hitches on a capture card** ([#20](https://github.com/smoovplay/smoovplay/issues/20)).
  When the PC fell behind for a moment and a frame was dropped, SmoovPlay lost count of which frames were new, and
  catching up a moment later showed as a hitch. Frames are now numbered as they come off the card, so a dropped one
  no longer moves the count.
- **The card's sound is found on AVerMedia cards** ([#23](https://github.com/smoovplay/smoovplay/issues/23)). Names
  ending in "-Video" and "-Audio" now match, and AVerMedia's extra sound device called "Not To Use" is never picked.
  Before, the two tied and SmoovPlay played the picture without sound.

**App window** (Experimental)
- **Capture keeps trying for 10 seconds when Windows takes it away**
  ([#21](https://github.com/smoovplay/smoovplay/issues/21)), for example when the screen changes mode. It used to
  give up after two seconds. The log now says Windows' reason in words.

**Setup**
- **SmoovPlay installs into a folder whose name has letters outside English**
  ([#24](https://github.com/smoovplay/smoovplay/issues/24)), such as Chinese, or an accented Windows user name.
  Setup's "Optimise for your graphics card" step failed there.

**Test mode**
- **Simulate a 30 fps game turns itself off on a game that's at 30 fps already**
  ([#26](https://github.com/smoovplay/smoovplay/issues/26)). It halved such a game to 15 fps, which stutters. It
  stays on through a 60 fps game's 30 fps cutscenes. In the video window's menu (M) it's now under Smoothness, next
  to Game frame rate.

**Diagnostics**
- **The log says how evenly the game's frames arrived** ([#25](https://github.com/smoovplay/smoovplay/issues/25)):
  frames that came late or bunched together point at Wi-Fi or the capture, not the PC.
- **Copy diagnostics includes your last game session's log**, also when copied right after setup.

## 0.3.9

**Display**
- **The screen no longer goes black mid-game.** Windows turns the screen off after a while without mouse or
  keyboard input (15 minutes on many PCs), and a controller doesn't count, so the screen could go black until you
  clicked. While the video plays, SmoovPlay now asks Windows to keep the screen on, as video players and games do.
- **Smaller stats overlays, out of the way of the game's HUD.** Both styles sit right in the corner, inside the
  margin games keep clear of their own HUD. Detailed is now a small box like Minimal's, not a strip across the top.
  New in Settings, Display (and the video window's menu, M): **Stats overlay position**, any corner, and **Stats
  overlay size**: Small (the default), Medium, or Large for a TV across the room.

## 0.3.8

**Settings**
- **Capture card resolution is greyed out unless a capture card is your source** (picked on the Play page), with a
  note saying why: PS5 Remote Play streams 1080p at most, and App window capture is scaled to 1080p. Before, you
  could pick 1440p or 4K with Remote Play and nothing changed. **Minimise chiaki-ng** is greyed out the same way
  unless PS5 Remote Play is picked. Switches that can't be changed now look it.

**Frame tap** (Experimental, for app developers)
- **Another app's frames aren't called chiaki-ng any more** ([#18](https://github.com/smoovplay/smoovplay/issues/18)).
  For an app sending compressed video through the frame tap, the log says "[tap] frames: ..." and the notes say
  "the app", and a chiaki-ng running on its own is no longer minimised.

## 0.3.7

**AMD and Intel graphics** (Experimental)
- **chiaki-ng draws with OpenGL on AMD and Intel graphics** ([#19](https://github.com/smoovplay/smoovplay/issues/19),
  a Radeon RX 9070 XT). chiaki-ng's default, Vulkan (which also decodes with Vulkan), crashed there. SmoovPlay
  shows the picture itself, so chiaki-ng's own window, hidden behind it, loses nothing. It's for SmoovPlay's
  sessions only: chiaki-ng's saved setting stays as it is. On NVIDIA nothing changes.

**Display**
- **Fill the screen, for ultrawide screens** (Settings, Display; key **Z** in the video window;
  [#19](https://github.com/smoovplay/smoovplay/issues/19)). Consoles only send a 16:9 picture, to a TV, a capture
  card or Remote Play, so there's no more of the game to show: this isn't real ultrawide, just different ways of
  laying out the same 16:9 picture. **Fit** keeps all of it with black bars (the default, as before). **Zoom** fills
  the screen by cutting off the top and bottom, which can cut the HUD. **Stretch** makes everything wider.
  **Panorama** keeps the middle close to its true shape and stretches the sides, like a TV's wide zoom. On the
  launcher's Settings page, a small drawing beside the choices shows what each one does on your screen's shape.

**Capture cards** (Experimental)
- **Capture card resolution: 1080p, 1440p or 4K** (Settings, Picture). It was always 1080p. Higher is sharper on a
  1440p or 4K screen, but every new frame is drawn at the size captured, so it can run quite sluggish: 1440p is
  about twice the work of 1080p (fine at 60 fps on most cards), 4K about four times (likely sluggish even at 60 fps
  on a fast card). 1080p stays the default. The console has to send that resolution too; a card without it uses the
  largest it has under it. Motion detail on Auto goes to half for 1440p at 90 fps, and to a quarter for 4K.

## 0.3.6

**Picture**
- **NVIDIA RTX VSR works in the installed app** (Experimental). The AI upscaler now comes with NVIDIA VFG's download
  from NVIDIA (RTX 40 and 50 series), so once NVIDIA VFG is set up, Settings → Picture → Upscaler → NVIDIA RTX VSR
  works, with nothing more to install. It runs an AI model on every frame, so at 90 and 120 fps it can cost some of
  the extra frames; AMD FSR 1 stays the default. On a capture card's uncompressed picture it uses NVIDIA's models
  for clean video. Not with HDR output yet.

**Capture cards** (Experimental)
- **Fewer pops in a capture card's sound, in heavy games too** ([#17](https://github.com/smoovplay/smoovplay/issues/17)).
  0.3.5 fixed most of the popping, but a little was left in a heavy game. Three things could still cause it:
  - SmoovPlay gave Windows only about 20 ms of sound at a time, and a busy moment in SmoovPlay could make the next
    lot later than that. Windows now gets about 50 ms, taken off the sound's delay, so it stays in step.
  - The card's sound is read along with the picture, so when that's held up for a moment the sound comes in a
    burst. SmoovPlay now keeps as much sound as the longest recent wait (up to 150 ms) ready to play.
  - When the picture's delay changed by more than 40 ms, the sound skipped or paused to catch up. Changes of up to
    120 ms now play the sound 1% faster or slower until it's back in step instead.
  If the sound does run out, it now fades out and back in, and pauses once until there's enough again, where
  before it could click several times in a row.
- **The log says how the sound played:** once a minute if anything went wrong, and for the whole session at the
  end. Copy diagnostics includes it.

## 0.3.5

**Capture cards** (Experimental)
- **No more popping in a capture card's sound** ([#17](https://github.com/smoovplay/smoovplay/issues/17), an Elgato
  HD60 X). The card's sound comes in 20 ms packets, and when the picture's delay was shorter than that (31 ms with
  NVIDIA VFG at 120 fps, less the sound output's own delay), SmoovPlay's sound ran out before the next packet: a
  short gap many times a second, heard as pops as loud as the game. It now always keeps a packet and a little more
  queued, so at worst the sound is about 20 ms later than the picture. The log says if the sound ever ran dry.

**Settings**
- **The launcher's Upscaler list says RTX VSR isn't in the installed app yet**
  ([#17](https://github.com/smoovplay/smoovplay/issues/17)). It needs NVIDIA's RTX Video SDK, which can't be
  included, so picking it used AMD FSR 1, and the list didn't say so.

## 0.3.4

**Dip smoothing** (Experimental, please send feedback)
- **New: Dip smoothing** (Settings → Smoothness, off by default), made to help 30 fps games that sometimes drop
  below 30, as big open-world games like GTA VI may at times on a base console. At each dip a frame stays on screen
  for 50 or 67 ms instead of 33 and the next one jumps, and the smooth motion around it makes the hitch stand out.
  With Dip smoothing on, SmoovPlay spaces the frames it draws by when the game's frames really arrived, so a dip
  looks like a brief slow-down instead of a freeze and a jump. It turns itself on after two dips within a second and
  off 3 s after the last, so a game holding a steady 30 is unchanged. Works with Game frame rate Auto or 30 fps.
- **It's experimental.** It's been tried on simulated dipping games and a stand-in PS5, not yet on a real game that
  dips. While it's smoothing it adds 17 to 50 ms of delay (more for longer dips, and more at 90 fps than at 60 or
  120), and every frame shown is a generated one, so the graphics card works harder: on a busy card some of the
  extra 90 or 120 fps frames fall back to 60 (in our test at 90 fps, 79-83% were shown against 97% with it off). If
  you try it, tell us how it went in a [GitHub issue](https://github.com/smoovplay/smoovplay/issues).

**Play**
- **The in-video menu is in sections, so it's no longer one long list.** Press M: the main page has the quick picks
  (Frame generation, Output frame rate, Upscaler) and a row for each section (Smoothness, Picture, Looks, Display,
  Instant replay, PS5 Remote Play), as on the launcher's Settings page. Enter opens a section and Esc goes back; M
  closes the menu from anywhere, and it opens again where you left it. It's about two thirds the height it was.
- **Auto tells a PS5 Remote Play game's new frames from its repeats by the picture**, as App window already did.
  It went by each frame's compressed size, which misread locked 30 fps games: a repeat can be nearly half a new
  frame's size, and after any still scene (a menu, a pause) the size check stopped answering for the rest of the
  session. Replayed over frame logs from real 30 fps sessions, Auto now calls 93-99% of repeats right over
  Ethernet (83% over Wi-Fi), where it called 3-35% right, and switches Even out 40-60 on by mistake for 0-5% of
  the time instead of 3-23%. Game frame rate 30 fps works as before.

**Fixes**
- **The delay figure, and Sound in sync, which follows it, are a little more exact with Even out 40-60 on.** A frame
  now counts as on screen when it's first drawn as itself, not only once its moment has passed, which for some
  games counted it up to a tick late (about 5 ms too much on average for a 45 fps game at 60 or 120 fps).
- **The end-of-session stream check no longer gets stuck after a still scene** and reports the game's frame rate
  from the whole session.

## 0.3.3

**Fixes**
- **Game frame rate 30 fps no longer judders back and forth when a game dips below 30.** When a game missed a frame
  (showing one for 50 ms instead of 33), the 30 fps setting could be a frame out of step for up to a second: it
  showed the game's next frame, then the in-between frame from before it. It now keeps every frame in order, one
  frame later until it's back in step. Auto wasn't affected.

## 0.3.2

**Play**
- **Steady HUD works with NVIDIA VFG too**, not only RIFE: the map, health bar and subtitles stay as the game drew
  them while the scene behind them moves. Still off by default (Settings, Smoothness). Thanks to
  [@Vadko](https://github.com/Vadko) ([#11](https://github.com/smoovplay/smoovplay/pull/11))!
- **Auto-detect tells RTX 40 and 50 series owners their card can run NVIDIA VFG**, with a button to set it up.
  Nothing is downloaded until you accept NVIDIA's licence, and once it's set up, Auto-detect times it and suggests it
  if it keeps up. Thanks to [@Vadko](https://github.com/Vadko) ([#7](https://github.com/smoovplay/smoovplay/pull/7))!

**Fixes**
- **Fullscreen keeps Windows' HDR on.** With Use HDR on in Windows, fullscreen switched the display to SDR while the
  video was in front and back to HDR with every Alt+Tab or click, so the colours kept changing (and fullscreen could
  look yellowish). With Windows' HDR on, the video window now uses the DXGI presentation, and fullscreen is a
  borderless window, so the display stays in HDR. Thanks to [@Vadko](https://github.com/Vadko)
  ([#6](https://github.com/smoovplay/smoovplay/pull/6))!
- **Each choice on the Settings page shows its own tooltip** as the mouse moves along a row (AMD FSR 1, NVIDIA RTX
  VSR, Bilinear), instead of the first one's staying up. Thanks to [@Vadko](https://github.com/Vadko)
  ([#10](https://github.com/smoovplay/smoovplay/pull/10))!

**Capture cards** (Experimental)
- **Sound from cards that carry it on their video device**, as the AVerMedia Live Gamer 4K (GC573) does
  ([#9](https://github.com/smoovplay/smoovplay/issues/9)). SmoovPlay only looked for a separate sound device named
  like the card; when there's none, it now tries the card itself, and plays the picture alone if that has no sound
  either.

**Setup**
- **A folder Windows won't let SmoovPlay write to is caught before installing**, such as a Program Files folder on
  another drive ([#9](https://github.com/smoovplay/smoovplay/issues/9)). Setup used to stop with an "access denied"
  error; it now tries writing there first (leaving nothing behind) and asks for another folder.

## 0.3.1

**Play**
- **Game frame rate: 30 fps** (Settings, Smoothness; and the in-video menu). For a game locked at 30 fps: every
  other frame is taken as new, so motion stays even whatever the scene. With PS5 Remote Play, frames are counted by
  the stream's own numbers, so frames lost on the way don't throw the rhythm off. On Auto, a still or dark scene could make
  SmoovPlay read a locked-30 game (Gotham Knights on PS5) as 40 fps or more, which made motion uneven and kept
  turning Even out 40-60 fps on and off. Simulate a 30 fps game was the way round it, but that's a test mode: it also
  scores every frame, which costs graphics card time at 90 fps. The new setting is saved, as everything else you
  change in the in-video menu is.
- **A frame log for each PS5 Remote Play session**, next to its log (frames-*.csv, the last 10 kept, about 1.5 MB per
  5 minutes, no pictures): each frame's size and timing and what SmoovPlay made of it, so Auto can be tuned on real
  games.

**Fixes**
- **Alt+Tab works while the video is fullscreen.** Fullscreen was an always-on-top window, so switching to another
  program left the video over it. It's now a borderless window over the monitor, as games' borderless mode.
- **The video window's keys work without a click again** when it opens behind chiaki-ng or the launcher. It took the
  keys only if nothing had been pressed or moved since it opened (moving the mouse counted), and only after
  chiaki-ng was minimised, which can leave no window to take them from. It now takes them as it opens, before
  minimising chiaki-ng, and the log says if it had to try again.
- **Esc no longer freezes the video window while a before & after video is being made.** The window closes straight
  away and the video carries on in the background; the launcher says when it's saved, with a button to show it.
  Update, Repair and Uninstall ask first while one is being made.
- **Stop in the launcher ends a session gracefully**, the same as Esc (a second Stop, or 20 s, forces it).
- **Results no longer say "about 60 fps, between 30 and 60"** when a game ran unevenly: they say how long it ran
  between 30 and 60 fps and that its frames were evened out.
- **chiaki-ng stays minimised after Alt+Tab.** It was only minimised in the stream's first seconds, so after
  switching to its window and back, its unsmoothed picture stayed up beside SmoovPlay's. Thanks to
  [@Vadko](https://github.com/Vadko) ([#5](https://github.com/smoovplay/smoovplay/pull/5))!
- **The PC check recognises 6 GHz Wi-Fi** (Windows 11), which it used to call 2.4 or 5 GHz. Thanks to
  [@Vadko](https://github.com/Vadko) ([#4](https://github.com/smoovplay/smoovplay/pull/4))!

**Capture cards** (Experimental)
- **Finding cards is sturdier** ([#2](https://github.com/smoovplay/smoovplay/issues/2), an Elgato HD60 X that
  wasn't listed):
  - SmoovPlay now reads device names from Windows' list without loading each camera's driver, as OBS does. Before,
    it opened every camera and virtual camera to see what it carried, so one faulty driver could stop the whole
    list (or Play) before the card was found. Only the card you pick is opened.
  - Cards that report video and sound together were dropped from the list without a word; they're listed now.
  - An empty list is looked at once more half a second later, and looking gives up after 30 s instead of waiting
    forever on a driver that doesn't answer.
  - The Play page says which it is: the list failed (and why), Windows has sound devices only, or there's no card.
- **When a card won't open, it says why**: in use by another app (many cards, Elgato's USB ones among them, work
  with one app at a time: close OBS or Elgato's apps first), blocked by Windows' camera privacy setting, or
  unplugged.
- **The log notes a card on a USB 2 port** (1280×720 at 30 fps at most) and a card offering 10-bit HDR, which
  SmoovPlay can't read yet: it uses 8-bit, so turn HDR off on the console if the picture looks washed out.

**App window** (Experimental)
- **The Xbox app is found again** ([#1](https://github.com/smoovplay/smoovplay/issues/1),
  [#3](https://github.com/smoovplay/smoovplay/issues/3)). Its current version runs as XboxPcAppCE.exe, which
  SmoovPlay didn't know, and Microsoft Store apps draw inside a frame window that SmoovPlay skipped. Both are listed
  now, under the app's own name.
- **When a streaming app is running but can't be picked** (in the tray, on another desktop, too small), the Play
  page says so and what to do.
- **The smooth picture fits the app's window.** It was drawn at 16:9 inside it, so a window of another shape (a
  browser's, say) looked squeezed, with bars over part of the app. It now lines up with the app exactly, even when
  the window hangs off the screen.
- **SmoovPlay's keys work while the app has the keyboard**: Ctrl+Alt with the usual key, from anywhere. Ctrl+Alt+M
  menu (then the arrows, Enter and Esc work in it until it closes), Ctrl+Alt+F fullscreen, Ctrl+Alt+Esc quit,
  Ctrl+Alt+S stats, Ctrl+Alt+I smooth on/off. Before, M, F and Esc went to the app, and only Stop in the launcher
  ended it. The video lists them when it starts and when the mouse moves over it, and the Play page has them in a
  card with **Change keys**: click one and press new keys. A key another program already has is skipped for the
  next one (Ctrl+Alt+Space for the menu, Ctrl+Alt+End to quit), and the Play page says so.

**Launcher**
- **Check for updates** under the version number on the left. The automatic check now runs every 6 hours, not 20.
- **Copy diagnostics** (and Send feedback) say more and share less:
  - the source you play from, its own PC check, and for capture cards the devices Windows lists, the card's driver
    state and USB IDs, and why a device list failed; for App window, the streaming apps found and a count of other
    windows (never their titles)
  - your Windows user name is left out of every path, whatever its capitals (some were missed before), and device
    names like "Alex's iPhone" lose the name
  - Windows 11 is called Windows 11 (Windows itself still says "Windows 10" in the place SmoovPlay read)
- **Frame tap is labelled "for developers"**, and points app users to App window: it only works with an app built
  to send frames to SmoovPlay.

## 0.3.0

**Play**
- **A minimal stats overlay**, now the default: a small line in the corner with the frames on screen, the game's own
  frame rate (amber when it isn't 30 fps) and the delay, and a dot: green, your PC keeps up; amber, it's tight; red,
  it's falling behind. Settings → Display → Stats overlay style has Detailed for everything as before; **S** goes
  Off, Minimal, Detailed.
- **Keys work in the video window straight away.** It no longer needs a click before M, F or Esc do anything, and
  chiaki-ng stays minimised when it shows its window again just after the stream starts. The video window shows its
  keys when it opens (M menu, F fullscreen, Esc quit), and again when you move the mouse over it.
- **The in-video menu stays still** while you go up and down it: the space for each setting's description is
  the same size whichever one is picked, so the rows no longer jump.
- **Test results say quality in plain terms**: the share of each frame that's visibly off from the real one (lower is
  better), instead of dB. It's only given when there's enough to go on: at 90 fps, or with a game that isn't at a
  steady 60, a few seconds of fair frames.
- **Instant replay at 120 fps keeps 60 fps clips** (every other frame). Encoding all 120 a second fell behind,
  dropping frames from clips, and took graphics card time from play.

**For developers** (Experimental)
- **The frame tap is documented for any app** ([docs/frame-tap.md](docs/frame-tap.md)): the shared memory
  chiaki-ng sends PS5 Remote Play's frames through. An app can now send decoded frames too (NV12 or BGRA), and
  say which are repeats, or send pictures only when they change. Pick **Frame tap** on the Play page (or
  `run.py --source tap`). `scripts/frametap_send.py` and `docs/frame-tap-writer.c` are writers to start from.
  Tested with those, not yet with another app.
- **A slimmer chiaki-ng patch**: the tap's code now lives in its own files (`chiaki_tap/frametap_session.cpp`), and
  chiaki-ng's own files only get one-line calls, so a new chiaki-ng version is quicker to patch.

## 0.2.0

**Experimental** (please send feedback)
- **HDR** (Settings → Picture → HDR, off by default). For a PS5 streaming in HDR, pick chiaki-ng's H.265 HDR codec
  and turn this on. **On, shown in SDR** gives the stream its real colours on any monitor, where before it looked
  washed out, for about 0.7 ms of graphics card time a frame. **On, HDR output** keeps the picture HDR10 all the way
  to a monitor with Windows' HDR on; Looks and RTX VSR are off with it, and instant replay clips are saved in SDR.
  Tried with a stand-in for a PS5's HDR stream, not a real one yet; HDR output was checked by reading back the
  10-bit picture it sends, not on an HDR screen.

**Play**
- An HDR stream with HDR off now says how to fix the washed-out colours.

**Launcher**
- The PS5 guide explains the HDR codec instead of saying to turn HDR off. HDR has the Experimental badge and a
  Send feedback button on the Settings page.

**Fixes**
- **Low delay** presentation could hand Windows a black buffer instead of the new frame on some refreshes (found by
  reading back what it sent). Each frame is now copied into place, for one quick graphics card copy.

## 0.1.0 (early release)

The first public version. Windows 10 or 11, with an NVIDIA RTX graphics card recommended.

**Play**
- Plays your PS5 on the PC over Remote Play, through chiaki-ng (the open-source Remote Play app, with SmoovPlay's
  frame tap built in). Nothing is installed on the console.
- A 30 fps game becomes 60, 90 or 120 fps: RIFE 4.22 lite draws the in-between frames (TensorRT or PyTorch on the
  graphics card), and repeats and scene cuts are found so they aren't blended.
- AMD FSR 1 scales the 1080p stream to your monitor (RTX VSR too, when running from source).
- **Sound in sync:** chiaki-ng holds the game's sound back to meet the picture.
- **Delay test:** press L while playing; SmoovPlay presses a button through chiaki-ng and times how long the PS5
  takes to show it.
- **Test mode:** try it with a 60 fps game, played as a 30 fps one, with every AI frame scored against the real one.
- When no picture arrives, the video window says why, for example that chiaki-ng isn't streaming yet.
- **Instant replay:** keep the last 30 seconds to 5 minutes and press R (or Ctrl+Alt+R anywhere) to save it as an
  MP4 at the frame rate you see, with the game's own sound from PS5 Remote Play or a capture card. B saves a before
  & after video: the game's own frames next to SmoovPlay's, split screen or side by side. Recorded on the graphics
  card's video encoder; off until you pick a length.
- **Looks:** stack effects like the nodes of a color grading app (color, color wheels, split tone, LUTs, bloom,
  halation, blur, clarity, chromatic aberration, sharpening, film grain, vignette, scanlines, letterbox, color-blind
  assist) and see the result live on the game while you edit. 14 looks to start from. K turns the look on and off
  and J goes to the next one. Share a look as a short code that anyone can paste. Clips stay clean unless you turn
  on Looks in clips.
- Optional NVIDIA Video Frame Generation (VFG) as the interpolation model on RTX 40 and 50 series cards: set up from
  the launcher, downloaded from NVIDIA under its licence, not part of SmoovPlay.

**Launcher**
- One installer: checks the PC, downloads the AI runtime from official sources, and walks you through pairing the
  PS5. Installs for your Windows user only, without admin rights.
- Settings with quick picks, and the rough picture and speed impact of each choice.
- **Updates itself:** when a new version is out, one click downloads it, checks its signature and installs it,
  keeping your settings, looks and clips.
- A Looks page to make, change and share looks, drawn as you edit on sample screenshots from GTA VI and five other
  games, and shown live on the video while you play.
- Opens where it was last left.

**Experimental** (built and tested with stand-ins, not yet on real hardware: please send feedback)
- **AMD Radeon and Intel Arc cards:** RIFE through DirectML. The same frames as the NVIDIA engine; on the
  development PC's NVIDIA card it took about 1.7 times as long as TensorRT. Not yet tried on an AMD or Intel card.
- **Even out 40-60 fps:** games whose frame rate wanders between 40 and 60 have each frame timed and evenly spaced
  frames drawn from them, instead of some staying on screen twice as long. Tried with real footage played as 43
  and 47 fps games, not yet with a real one.
- **Capture cards:** any console with HDMI, through a capture card, with its sound held back to meet the picture.
  MJPEG cards are decoded on the graphics card. On a PS5, turn off HDCP while you use one.
- **App window:** the window of the app you stream with (the Xbox app, Sony's PS Remote Play app, Moonlight, a
  browser playing a cloud game), captured on the graphics card and shown smoothly over it. The repeat check learns
  the stream's look and keeps to a 30 fps game's rhythm.
- **Low delay** window presentation (a DXGI flip-model swap chain).

**Known limits**
- The installer isn't code-signed yet: Windows shows "Windows protected your PC" (More info, then Run anyway). The
  README explains why, and how to check the file is the real one.
- Tested on one PC so far, with an RTX 4070 Ti SUPER. Other RTX 30, 40 and 50 series cards should work; RTX 20
  cards and laptops aren't tested yet.
- If the picture stays black, or the PS5 says the content can't be shown, turn off HDCP on the PS5 (Settings →
  System → HDMI) and turn it back on when you're done.
- With an app window, the app's sound comes a little ahead of the smooth picture.
- See the README's Known limits for the rest.
