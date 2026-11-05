# Floating Trash Can

A small always-on-top trash can for Windows that you drop files and folders onto, so you can clear your desktop quickly and still undo every drop.

## What it does
- Moves every file you drop into one trash folder (by default `%USERPROFILE%\TrashCan`, for example `C:\Users\<you>\TrashCan`).
- Empties a dropped folder: every file inside it, at any depth, goes into the trash folder, and then the empty folders are removed.
- Checks for real duplicates. If a file with the same name and the exact same content is already in the trash folder, the dropped copy is deleted. If the content is different, the drop is saved as `name (1).ext`. Nothing is ever overwritten.
- Records every drop in a history, so you can undo any drop later, not only the last one.
- Stays sharp at any size and on 1080p and 4K screens, and remembers its size and position.

## What you need
- Windows 10 or 11.
- Python 3.10 or newer (from python.org).
- An internet connection the first time you start it (to download PySide6, the window toolkit).

## Install
1. Download this project (green **Code** button, then **Download ZIP**) and unzip it anywhere you like.
2. Double-click **`TrashCan.pyw`**.
3. The first time only, a small window called **Setting up for first use** appears and installs PySide6 (`PySide6-Essentials`). This takes a minute or two. If you prefer to install it yourself first, open a Command Prompt in the folder and run `pip install -r requirements.txt`.
4. The trash can appears near the bottom-right corner of your screen.

### Optional: a single .exe
If you want one file that runs without Python, run this once in the project folder:

```
python tools\make_exe.py
```

It installs PyInstaller if needed (it prints each step) and creates `dist\FloatingTrashCan.exe`. You can copy that file to any Windows PC and double-click it.

## How to use
1. Drag files or folders from File Explorer or the desktop and drop them on the can. The lid opens when you drag over it.
2. Read the short result under the can (for example "2 moved"). A tray pop-up also appears, if pop-ups are turned on.
3. To undo the most recent drop, right-click the can and choose **Undo last drop**.
4. To undo an older drop, right-click the can and choose **History and undo...**. Pick one or more drops in the list, click **Undo this batch** (or **Undo selected**), then click **Yes**.
5. Double-click the can to open the trash folder.
6. To quit, right-click the can and choose **Quit**.

Other things you can do with the window:
- **Move it** by dragging its middle.
- **Resize it** from any edge or corner. Corners keep its shape; edges change it.
- **Right-click** for the menu: **Open trash folder**, **Undo last drop**, **History and undo...**, **Always on top**, **Notifications**, **Opacity**, **Reset size**, **Settings...**, **Quit**.
- If the can is ever off-screen, double-click its tray icon (or choose **Show trash can** in the tray menu) to bring it back.
- During a big drop the can shows a progress ring and a count, and the menu shows **Stop this batch**. Files already moved when you stop are kept in the history and can be undone.

## Settings and options
Right-click the can and choose **Settings...**:

| Setting | What it means |
| --- | --- |
| Trash folder | Where drops go. The default is `%USERPROFILE%\TrashCan`. Click **Browse...** to pick another folder. |
| Window opacity | How see-through the can is. |
| Float above all other windows | Keeps the can on top of other windows. On by default. |
| Also compare against earlier "name (1)" copies | Also checks the numbered copies of a name, so dropping the same file three times leaves only one copy. On by default. |
| Pop-up notifications | **After every drop**, **Only when something goes wrong**, or **Never**. The result is always shown under the can. |
| Ask before drops over | Asks you to confirm a drop with more than this many files. Set it to 0 to never ask. The default is 500. |
| Start with Windows | Starts the can when you log in to Windows. |

Settings and the undo history are stored in `%APPDATA%\FloatingTrashCan`, not in the trash folder. Emptying the trash folder does not erase your history.

### Command line
The same engine also works without the window:

```
python -m trashcan.cli trash "C:\Users\me\Desktop\old stuff"
python -m trashcan.cli list
python -m trashcan.cli undo last
python -m trashcan.cli undo 3f9a1c2b4d5e
```

## Safety notes
- **What it moves:** every file you drop goes into the trash folder. Dropped folders are emptied and then removed, but only when they are completely empty.
- **What it deletes:** a dropped file is deleted only when a file with the same name and exactly the same bytes is already in the trash folder (checked with a SHA-256 hash). Undo restores it from the kept copy.
- **Undo:** every drop can be undone from **History and undo...**. Undo puts files back at their original path and name, and recreates removed folders. If something is now in the way, the restored file is saved as `name (1).ext` instead of overwriting.
- **The trash folder is not the Windows Recycle Bin.** Files stay there until you delete them yourself. If you delete files from the trash folder by hand, those drops can no longer be fully undone.
- **Quitting during a drop or undo:** the can asks you first. If you click **Yes**, it stops the drop, saves what was already moved to the history, and then quits.
- **Windows settings:** **Start with Windows** adds one entry, `FloatingTrashCan`, under `HKEY_CURRENT_USER\Software\Microsoft\Windows\CurrentVersion\Run`. Untick it to remove the entry.
- **Installs:** the first start installs PySide6 with pip, as shown in the setup window. Nothing is sent anywhere else.

## Troubleshooting
- **The setup window says it could not install PySide6.** Check your internet connection, then open a Command Prompt in the project folder and run `pip install -r requirements.txt`.
- **The can is not visible.** Double-click the tray icon, or right-click it and choose **Show trash can**.
- **A drop says "busy - try again in a moment".** Another drop or an undo is still running. Wait until the progress ring is gone.
- **Some files were not moved.** They were probably open in another program. Open **History and undo...**, select the drop, and read the "failed" rows. Close the other program and drop the files again.
- **An undo says "Partly undone".** Some files could not be put back (for example, they were locked). Run the undo again later; items already back in place are skipped.

## How it works
The window is a frameless PySide6 (Qt) widget that draws the can as vector art, so it scales cleanly with per-monitor DPI. Each drop runs on a `QThread` worker, and undo also runs on a worker thread, so the window never freezes. Duplicates are found by comparing file sizes first and then SHA-256 hashes, and paths longer than 260 characters use Windows extended-length paths. Every drop and undo is written to an append-only JSON Lines journal, so a crash can at worst lose the one record being written.

### For developers
```
pip install -r requirements.txt pytest
python -m pytest
```

| File | What is in it |
| --- | --- |
| `trashcan/core.py` | Moving, the duplicate check, flattening folders, undo |
| `trashcan/journal.py` | The append-only history |
| `trashcan/app.py` | The floating window |
| `trashcan/history.py` | The history and undo window |
| `trashcan/icon.py` | The vector trash can |
| `trashcan/settings_dialog.py` | Settings |
| `trashcan/cli.py` | Command line |
| `trashcan/bootstrap.py` | The first-run PySide6 install |
| `trashcan/longpath.py` | Windows paths over 260 characters |
| `TrashCan.pyw` | The double-click starting point |

## License

MIT. See [LICENSE](LICENSE).
