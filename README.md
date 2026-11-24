**日本語版：[README.ja.md](README.ja.md)**

# VPT39 — Live video playback + projection mapping

**What this app is:** a video player made especially for projection mapping.
It is designed to be easy to use for media and interactive art works that use projectors, live shows and similar events.

It is a small app for live use that combines VPT's "four-corner mapping" with the "cue playback and layer compositing" of a VJ app.

The control panel can be switched between Japanese and English with the 日本語 / English button in the top bar (the choice is remembered).

**VPT39 was inspired by VPT ([VPT 8](https://hcgilje.wordpress.com/vpt/), by HC Gilje).** The "39" in the name is read "san-kyu" in Japanese, which sounds like "thank you". HC Gilje has given permission to use the name VPT.

> VPT39 is an unofficial tool inspired by VPT (VideoProjectionTool) by HC Gilje. It is not affiliated with VPT in any way.

- **Output**: as in VPT, drag the output window to the display you want to project on, then press "Fullscreen"
- **Control**: the control panel that opens in the browser on your laptop (`http://localhost:8080/`)
- **Manuals** (`manual/en` folder)
  - `quickstart.pdf`: a double-sided A4 quick start (front = preparation, back = during the show). Keep it next to your equipment during the show
  - `concept.pdf`: two A4 pages explaining how it works (with diagrams)
  - `guide.pdf`: user guide (detailed version, A4). From preparation to the show, troubleshooting and checklists, with numbered screenshots
  - `index.html`: screen-by-screen manual. Shows, with numbers, how the picture on the projector matches the control panel
  - The PDFs are made from the .html files with the same names. After editing one, open it in Edge and use "Save as PDF" (no margins, background graphics on)

## Features of VPT39 (improvements over VPT8)

Note: tested on Windows 11.

1. **Any number of outputs**
   You can add as many output windows as you like. You can use as many monitors and projectors as your graphics card can connect, with a different picture and different mapping for each output.
2. **Mapping from a phone**
   You can adjust the position of the projected picture (four-corner mapping) from a phone while standing in front of the screen. No app to install: just scan a QR code.
3. **Synchronized playback on several outputs**
   One video can be shown on several outputs at the same time, perfectly in sync. Clips lined up for each song or scene can be started from the beginning on all layers at once with one button ("Play column").
4. **Window capture**
   Any window on the same PC can be used directly as media, even from apps that do not support Spout (Zoom, a browser and so on). Receiving from Spout (TouchDesigner, Processing and so on) is also supported.
   Note: window audio cannot be captured at the moment (picture only).

### Other features

- **Audio mixer**: plays the sound of the videos on all layers at the same time, with volume and mute per layer and per output.
- **Capture board input**: brings in the picture and sound of another PC with low latency. Several units of the same model can be told apart.
- **Photo wall**: shows the photos in a folder automatically, newest first. You can also line up images from your media and show them in a fixed order.
- **Move photos in real time**: even during the show, you can adjust the position of photos and videos with the mouse or a phone.
- **Prepare on another PC**: if you set the "Show resolution" for each output, the shapes you adjust in a small window can be used as they are in the show.
- **Setup files**: for each venue or show, save the running order, mapping, volume and outputs together and switch between them.
- **Safeguards for the show**: auto-save, output windows do not close with ×, outputs open fullscreen on the same display at the next start.
- **Browser control panel**: tabs can be split into separate windows, and it comes with a manual whose numbers match the screen.
- **Media formats**: animated GIF, APNG and WebP, and transparent PNG can be used as they are.
- **Free, MIT license**: written in Python, so anyone can modify it.

### What it cannot do yet, compared with VPT8

- No mesh warping (fine correction for curved surfaces and so on). Mapping uses the four corners only.
- No masks (cutting out part of the picture).
- Cannot be controlled from external hardware such as OSC or MIDI controllers, LFOs or Arduino sensors.
- Cannot send video to other apps with Spout or Syphon.
- Windows only; does not run on Mac or Linux.
- Cannot capture audio from window capture.

## What it can do

| Feature | Description |
|---|---|
| Cue playback | Line up videos and still images per layer in running order, and click to switch clips |
| Clip list | Like a VJ app, each layer's clips are shown as tiles with thumbnails. Drag to add and reorder; "Play column" plays the clips with the same number on all layers at once |
| Mini controls | Under each thumbnail: pause, restart, stop, and drag the playback position bar (seek) |
| Multiple outputs | Add as many output windows as you like, each fullscreen on any display. One layer can be shown on several outputs at the same time (mapping is set per output) |
| Blank clip | A clip that shows nothing and also stops the sound. Lets a layer rest while other layers play |
| Layer picture | Uses another layer's picture as media. Shows the same picture at the same time in another place or on another output |
| Camera input | Brings in cameras and capture boards (picture and sound of another PC) with low latency. OBS Virtual Camera also works |
| Window capture | Brings in the picture of any window on the same PC (Zoom, Discord, a browser and so on) (no audio) |
| Spout | Receives video from TouchDesigner, Processing, OBS, VJ apps and so on, with no delay and with transparency |
| Mapping from a phone | Turn it on with the "Phone" button in the top bar and scan the QR code that appears; a phone on the same Wi-Fi can then adjust the four corners with an arrow pad (with hold-to-repeat and undo) |
| Adding and removing media | Drop files from your PC onto the media area to copy them into media. ✕ moves them to the Recycle Bin |
| Animated images | Animated GIF, APNG and WebP, transparent PNG |
| At the end | For each cue, choose "Loop / Hold last frame / Clear at end / Go to next (playlist)" |
| Layer compositing | Any number of layers. Blend modes: Normal, Add, Screen, Multiply. Opacity, show/hide and stacking order can be changed |
| Four-corner mapping | Move the four corners of each layer to fit a trapezoid or the shape of the screen. With edge feather and a test pattern |
| Photo wall | Shows the photos in a chosen folder one after another. New photos are shown first. If you line up images from your media in the row, they are shown in that order (if there is only one, it stays on). Portrait and landscape photos are not distorted. 8 transitions (fade, cut, slide, wipe, zoom) |
| Audio mixer | Plays the sound of all layers and all outputs at the same time. Faders and mute for the audio master → each output → each layer, with level meters |
| Display layout and identify | Shows a diagram of the connected displays with their arrangement, resolution and scale. "Identify" shows a number on each display; "Display settings…" opens the Windows settings |
| Saving | Settings are saved automatically to `project.json` (2 seconds after a change) |
| Setup | Save each venue or show's running order, clips, mapping, volume and output settings to a folder you choose. Manage files in VPT39's `settings` folder from the setup list |
| Show resolution | Set the resolution of the show projector for each output. Even if you prepare on another PC in a small window, it can be used as it is in the show |

## Setup (Windows, first time only)

1. Install **Python 3.13 (64-bit)**. During installation, check "Add python.exe to PATH" (Spout does not work with 3.14 or later. See chapter 2 of manual\en\guide.pdf for illustrations).
2. Install **VLC media player (64-bit)**. It is needed to play videos with sound. Without it, only the picture is played.
3. Double-click **`setup.bat`** in this folder. The required libraries are installed automatically.

## Starting

- **Double-click `run.bat`** to open all outputs as windows.
- To restore the saved fullscreen layout for a show, run `.\run.bat --fullscreen` in PowerShell.

Use `setup.bat` to install libraries. When VPT39 starts, the control panel opens in the browser. If it does not open, open `http://localhost:8080/` in your browser.

## Where to put media

- `media` folder: videos for each song (mp4, mov and so on) and images (png, jpg). Subfolders are fine.
- `photos` folder: photos for the photo wall. We recommend putting only photos here that a person has checked and approved.

## Panels on the right

- Use the tabs on the right to switch between **Media**, **Mixer**, **Layer settings**, and **Output settings**. The toolbar’s **Settings** menu also lists the panels.
- Use **×** in the panel header to hide the right panel. Choose a panel from **Settings** to show it again. **↗** opens the panel in another browser tab and hides it on the main screen. **Main panel** closes the separate tab and restores the panel on the original main screen.
- Use **Settings** to switch between Japanese and English.
- Choose **Save now** to save the current state immediately, or **Save as** to choose a folder and file name in the Windows save dialog.
- Choose **Open setup** to open a setup from the `settings` folder or browse for a file elsewhere. Changes to the active setup are saved automatically.

## Decks

- Click a deck tab below the clip grid to select it, or right-click it to rename or delete it.
- Right-click a deck tab to add a deck before or after it. To reorder decks, hold a tab, drag it until the insertion marker appears, then release it.
- Each deck saves its own video-layer clips, clip settings, and columns. Layer settings, output settings, and photo walls are shared. Any video or audio playing when you switch decks keeps playing.
- Your existing clips and columns appear in the first deck.
- A deck, column, or layer can be deleted when there is more than one.

## Adding clips (thumbnail list)

- In "**Files**" on the "**Media**" tab, the videos and images in media are listed with thumbnails (they appear a few seconds after you add them).
- Switch to "**Blank, layers, inputs**" to see the following special media. Add them by clicking or dragging, just like files.
  - **Blank**: clears the picture and stops the sound.
  - **Layer picture** (L1, L2…): shows the current picture of that layer again, at the same time. It also works when the source layer's "Show" is off (the sound plays on the source layer, so it is not doubled). A layer cannot show its own picture.
  - **Cameras and capture boards**: a list of connected devices. In the "No audio" field of the added clip, you can choose an audio input to play with it (such as the capture board audio). After connecting a device, press "Refresh list".
  - **Window**: a list of the windows open now. Only the picture is captured. It may stop when minimized, so keep the window open.
- You can also add a blank clip with "**+ Add blank**" at the end of a layer's clip row.
- **Click** a media item to add it at the end of the selected layer.
- **Drag** a media item onto a layer's clip row to add it to any layer at any position.
- Drop a video or image from the media panel onto the **center** of an existing clip to replace it. A yellow outline indicates replacement, and a yellow line near an edge or gap indicates insertion.
- Drop media from the media panel or File Explorer between clips in a video layer to insert a column. Other video layers retain their alignment, and an empty slot leaves that layer playing when the column is triggered.
- Drop one file from File Explorer onto an existing clip to replace its media in place. Click the updated clip when you are ready to switch playback. Drop on the empty tile at the end to add new clips.
- In video layers, drop a clip onto another clip's center to move it with its settings, overwrite the destination, and leave the source slot empty. A drop near an edge or in a gap reorders within the same layer or moves the clip to an inserted column in another video layer.
- Thumbnails are saved in the `.thumbs` folder (if you delete it, it is rebuilt automatically).

## Controls during the show

- Click a **clip thumbnail** to switch to that media.
- Right-click a clip to clear its slot. Right-click the blank space to the left of the column numbers to append a column, or right-click a column number to rename it, insert before or after it, or delete it.
- Right-click the layer name area to rename, add, or delete a layer.
- Right-click a clip and choose **Clip settings** to adjust volume (0–100%) and separate video and audio fades (0–30 seconds each). Changes apply during playback and save automatically.
- Use **Flip** in **Layer settings** to flip the layer horizontally or vertically. This applies to the layer across all decks and to photo walls.
- Press and hold a layer name, then drag it up or down to reorder the control panel without changing the output stack. Press and hold a column header, then drag it left or right to move the name and all its clips together.
- **Play column 1, 2, 3…**: plays the clips with the same number on all video layers at once (handy if you line up a column for each song).
- **Mini controls under the thumbnail** (video clips only)
  - Playback position bar: shows progress in green while playing and orange while paused. **Drag it to move the playback position**
  - ⏮: restart (go back to the beginning and play)　▶ / ⏸: play / pause　⏹: stop (clear that layer)
  - Dragging the bar or pressing ⏮ on a clip that is not playing starts playing that clip
- Choose a number under **Layer order** in **Layer settings**. Position 1 is the back of the output stack. The layer list stays in its original order.
- Use **All-layer brightness** in the top bar to adjust the whole picture. Adjust volume in the audio mixer.

## Audio mixer

- The sound of all video layers **plays at the same time**, whichever output (display) they are shown on.
- Volume is multiplied in this order: **Audio master** → **each output** (Output 1, Output 2…) → **each layer**.
  - Each fader sets the volume, and the **M** button mutes (red = muted).
  - The green bar under each layer shows the current playback volume.
- Hiding a picture keeps its sound playing. Use the **M** button to mute it.
- The previous clip keeps playing until the first frame of the new clip is ready. Picture and sound then switch together.
- Audio is output with Windows DirectSound (to allow a separate volume for each layer).

## Mapping (adjusting the four corners)

1. Open **Layer settings** and select **Edit** under **Mapping**.
2. In the diagram on the right of the control panel, drag the corners of the selected layer. Drag inside to move the whole picture.
3. For final fine-tuning, working **on the output window** is the most accurate.
   - Drag a corner with the mouse
4. "Test pattern" shows a grid, diagonals and circles, which you can use to check for distortion.
5. Use **Finish editing**, or close the right panel or its separate tab, to clear the mapping outlines and test pattern.
   - While editing, press **T** on the output window to toggle the test pattern, **R** to reset the corners, or **E** to finish. **E** also finishes editing from the control panel.

## Output windows (used the same way as in VPT)

- In **Settings → Output settings**, set each output’s **Output FPS limit** to **30 FPS** or **60 FPS** (default).
- At a 60 FPS output limit, a 30 FPS clip holds each frame until the next source frame arrives. Playback speed and audio stay the same.

1. In Windows "Display settings", choose "Extend these displays".
2. When VPT39 starts, a small window called "VPT39 Output 1" opens. **Drag it to the display (projector) you want to project on.**
3. Press **Fullscreen** under **Output settings** to fill the display. With the output window focused, you can also press **F** or **F11**, or double-click the output window. Repeat the action to return to the smaller window.
4. To add displays, open Output 2, Output 3… with "**+ Output window**", then move each one to the display you want and make it fullscreen in the same way.
5. Choose where a layer is shown with **Layers shown** under **Output settings**, or with **Output destinations** under **Layer settings**.
6. The mapping diagram and the four-corner adjustment are per output. For a layer shown on several outputs, switch with "Output to adjust" above the diagram (grabbing a corner on an output window also switches to that output).

- Opening an older wide-output setup makes each window an independent output. Review each layer’s **Output destinations**.

- The output resolution is normally the size of its window (the display's resolution when fullscreen). If you choose a "**Show resolution**" in the "Outputs" tab, it draws at that size regardless of the window size (if the shape differs, it is shown scaled down with black bars). The four corner positions are stored as proportions, so the shape stays the same when you change the window size.
- The display used for fullscreen is remembered, so the next time you start with `run.bat --fullscreen`, it opens fullscreen on the same display.
- "**Bring back**": turns the output back into a window and brings it to the middle of the main display (for when it has ended up somewhere you cannot see).
- Output windows do not close with × (to prevent accidents during the show). To remove one, delete it with ✕ in the control panel.
- You can add as many outputs as you like. Each video is loaded only once and shared with all outputs, so adding outputs does not increase video loading.

### Display layout and identify

- Below the "Output windows" section, a diagram shows the connected displays in their arrangement (the same as in Windows display settings), with resolution and scale. A green frame marks a display with an output window.
- "**Identify**": shows the control panel's number (display 1, 2…) large in the middle of each display for 4 seconds. The Windows number is shown too (click to hide).
- "**Display settings…**": opens the Windows "Display" settings. Change scale, resolution and display arrangement there (changing them during the show shifts the outputs, so do it before the rehearsal).
- Setting projector displays to 100% scale is the safest.

### NDI output

1. When copying VPT39 to another sending PC, include the `runtime/ndi` folder. VPT39 loads the NDI runtime from that folder. For a Git checkout without that folder, use the [NDI 6 Runtime](https://ndi.link/NDIRedistV6) or [NDI Tools](https://ndi.video/tools/).
2. In **Output settings**, choose **+ NDI output** to send without a local window, or change an existing output's type to **NDI only**.
3. Enter the source name under **NDI sender settings**. It takes effect when the field loses focus or you press Enter. Assign layers to each named source using the layer's **Output destinations** or the layer checklist in **Output settings**.

Choose **Output windows** to switch to a local display.

On a PC on the same network, select the source names in a receiver such as NDI Studio Monitor. Each source carries one composited video at its output's resolution and frame rate. Audio plays on the sending PC.

[NDI website](https://ndi.video/) · [Runtime files and distribution terms](THIRD_PARTY.md#ndi-について)

## Main settings in project.json

Press **Ctrl+S** while the output window has focus to save the current settings.

```json
"outputs": [{"rect": [80, 80, 960, 540], "fullscreen": true, "fs_rect": [1920, 0, 1920, 1080], "volume": 1.0, "mute": false}],
"audio_master": 1.0,
"web_port": 8080,
"allow_lan": false,
"media_dir": "media"
```

- `outputs`: settings for each output window. `rect` is the position and size as a window; `fullscreen` and `fs_rect` are the display it was made fullscreen on. These are updated automatically by the control panel and by moving the windows, so you do not need to edit them by hand.
- The old format (`"output": {"display": 1}` and so on) is converted automatically at the first start (fullscreen on that display).
- `allow_lan`: whether to allow control from a phone or tablet. Switching it with the "**Phone**" button in the top bar of the control panel saves it here (no restart needed). When it is on, a QR code appears; scan it with the phone camera. There is no password, and anyone on the same network can control it, so do not use it on a venue's shared Wi-Fi, and turn it off when you are done.
- The "Show resolution" of each output (`res`) and the name of the open setup (`setting_name`) are also stored here.

## Using with a photo wall

In the "Folder" of the photo layer, choose a folder that receives approved photos (for example, an "approved" folder in Dropbox). When a new photo arrives, it is shown first within a few seconds. To avoid files that are still being written, it waits 1 second after a file is saved before loading it.

Drag images from your media onto the photo wall row to show them from left to right. Click an image to start from it. Empty the row to go back to the photos in the folder.

Right-click the photo wall layer name to play, pause, play from the start, or stop. In **Photo playback settings**, set the time per photo and turn looping on or off. When looping is off, the last photo stays on screen. Set the time to 0 seconds to disable timed advances. Photo playback runs independently of **Play column**.

## Notes and limitations

- Videos are stretched to the layer resolution (1920×1080 by default). Export media that is not 16:9 beforehand.
- Before the show, **always do a full rehearsal on the same PC with the same projectors**. At the first start, a Windows Firewall prompt may appear.
- If playback is heavy, exporting videos as H.264, 1080p, 30fps makes it stable.
- Camera input: the same camera cannot be opened by two clips at the same time (to show the same camera in another place too, use "Layer picture"). The sound comes from the Windows "default speaker".
- Window capture: audio is not captured. Copy-protected video (such as streaming services) may appear black.

## Test media

Choose **Test media** in the Media panel to access the included test videos and images.

`media\テスト用` contains videos for testing (count + sound, a 60fps grid, color bars, wide 3840×1080, left/right sound), an animated GIF and a transparent PNG, pure white and pure black (to check the brightest and darkest), white and black with grids and checkerboards (for calibration), and a moon (perfectly round, to check the aspect ratio). `photos\テスト用` contains photos of different shapes and the moon. To rebuild them, run `.venv\Scripts\python.exe tools\make_test_media.py` (requires the ffmpeg component `imageio-ffmpeg`).

## Giving it to other people

Give them `VPT39_配布用.zip`. The recipient extracts the ZIP and follows the steps in "Setup (Windows, first time only)" (environments such as `.venv` are not included, so they are rebuilt with `setup.bat`).

## License

VPT39 is distributed under the **MIT License** (full text in [LICENSE](LICENSE)).

- Anyone can use it freely: use, copy, modify, distribute, and use it in works and events (including paid ones).
- The only condition is that when you distribute copies or modified versions, you keep the copyright notice and the LICENSE text.
- The author does not guarantee that it works and is not responsible for problems caused by using it. Always check it in a rehearsal before the show.

The licenses of the components it uses (VLC and others) are in [THIRD_PARTY.md](THIRD_PARTY.md).
