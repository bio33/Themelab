# ThemeLab — Android Customization Studio

A local Windows desktop-web app for managing multiple Android phones via ADB. **MVP v0.1** provides device discovery, serial-targeted actions, on-demand screenshots, theme management, wallpaper upload, and wallpaper staging + opening the system wallpaper picker.

**Important:** This version **does not automatically apply wallpapers, icon packs, live wallpapers, or Good Lock settings**. Applying a wallpaper is a manual confirmation on the phone. A future optional Android companion app can use the official `WallpaperManager` API for static wallpaper application, but live wallpaper and system-wide icons have extra platform limitations.

## Prerequisites (Windows)

1. Install **Python 3.11+** and **Node.js LTS**.
2. Download **Android SDK Platform-Tools** from https://developer.android.com/tools/releases/platform-tools and extract it. Add its folder to Windows PATH, **or** put it under `tools/platform-tools/` in this project. If located elsewhere, set `ADB_PATH` to the absolute path of `adb.exe`.
3. On the phone, enable **Developer options** (Settings > About phone > Software information > tap Build number 7 times), then enable **USB debugging**. Connect via USB and accept the computer's authorization prompt. Only use a computer you trust.
4. Confirm in PowerShell: `adb devices -l` — the phone must show status `device`, not `unauthorized`.

## Start

Double-click **`start_windows.bat`**. It sets up a Python virtual environment and frontend packages on first run, starts the FastAPI backend and Vite frontend in separate terminal windows, and opens http://127.0.0.1:5173.

Manual alternative (two terminals):

```powershell
# Terminal 1 (from project folder)
py -3 -m venv .venv
.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

# Terminal 2 (from project folder)
cd frontend
npm install
npm run dev
```

## Try the first flow

1. Connect your Galaxy S22 Ultra, then click **Refresh devices**.
2. Choose the phone (the app addresses it by **ADB serial** so a second connected Android phone will not receive its commands).
3. Click **Capture screen** to verify the connection (optional).
4. Create a theme (for example, *Neon City*).
5. Generate a vertical wallpaper in ChatGPT and download it. Import the image into the theme.
6. Click **Send wallpaper to phone**. The app stores it under `Pictures/ThemeLab/<theme-id>.png` on that phone.
7. Click **Open picker on phone**, browse to the transferred image and confirm it. Some Samsung/Android versions have different wallpaper picker menus; if the picker action is unsupported, open the phone's wallpaper settings or Gallery manually.

## Design

```text
frontend/                  React + Vite local UI
backend/app/main.py        FastAPI HTTP handlers and persisted theme manifests
backend/app/adb.py         Device enumeration and explicit -s SERIAL ADB commands
backend/app/profiles.py    Manufacturer capability registry (Samsung / generic)
data/themes/<id>/          Theme manifest and wallpaper PNG (local only)
start_windows.bat          Simple Windows launcher
```

All state is stored locally. No cloud generation, login, or remote server is included in this MVP. API only accepts CORS requests from localhost frontend; bind the server to **127.0.0.1** as shown. The server is not designed for exposure over a network.

### Safety and limitations

- Never connect a phone to an untrusted PC, and disable USB debugging when you are finished.
- The app performs only fixed ADB commands. There is no arbitrary command execution API.
- The screenshot endpoint may reveal personal information displayed on the device; screenshots are held in the browser and never written to server disk.
- Wallpapers are stored in a local theme library and pushed to `Pictures/ThemeLab` on the selected phone. No user data is pulled from the phone.
- ADB cannot reliably manipulate Samsung Good Lock or system-wide icons via a documented cross-device API. These will need per-OEM integrations, a compatible launcher, user-approved UI automation, or a companion app.
- Stock Android public APIs can set static wallpapers when called by an appropriately-permissioned Android app; ADB by itself is not a portable one-click wallpaper-setting API.
- Live wallpaper requires an installed `WallpaperService` implementation. The system offers a preview/confirmation flow, and devices may handle that differently.
- There is no guarantee that a phone can provide its current wallpaper for backup. Do not claim this MVP can fully restore existing themes.

## Roadmap

- **v0.2:** Android companion APK, install over ADB, static wallpaper **apply** using `WallpaperManager` and `SET_WALLPAPER`. Show result/error per device.
- **v0.3:** Motion wallpaper engine + preview/confirmation flow, matching icon assets, full theme package ZIP.
- **v0.4:** OEM capability modules (Samsung Good Lock guidance, Pixel launcher integration, etc.) and reusable automation recipes.
- **v0.5:** Optional authenticated ChatGPT image-generation integration, thumbnails, versioned theme packs, device-specific adaptation.

Official Android documentation:
- ADB: https://developer.android.com/tools/adb
- Platform Tools: https://developer.android.com/tools/releases/platform-tools
- WallpaperManager: https://developer.android.com/reference/android/app/WallpaperManager
