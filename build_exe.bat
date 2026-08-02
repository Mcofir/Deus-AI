@echo off
setlocal

REM ============================================================
REM  build_exe.bat
REM  1) Builds a single executable (dist\Deus.exe) with PyInstaller.
REM  2) If Inno Setup is installed, also builds a real installer
REM     (installer_output\DeusSetup.exe) that regular people can
REM     just run and click "Next, Next, Finish" - with a Start
REM     Menu shortcut, optional desktop shortcut, and a proper
REM     uninstaller under "Add/Remove Programs".
REM
REM  If Inno Setup is not installed, this script still successfully
REM  builds dist\Deus.exe (you can hand that single file to people
REM  directly - it works, just a less "polished" install experience).
REM  Inno Setup is free: https://jrsoftware.org/isdl.php
REM
REM  Usage: just double-click this file (or run it from cmd).
REM  Important: make sure a Conda (base) environment is NOT active -
REM  if you see (base) in your prompt, run "conda deactivate" first.
REM
REM  Note (fix): this file intentionally contains ONLY plain English/
REM  ASCII text (no Hebrew) - Windows batch scripts (cmd.exe) have
REM  notoriously unreliable Unicode/UTF-8 support, which caused
REM  garbled/split command errors before. Plain ASCII parses
REM  identically on every Windows system regardless of locale.
REM
REM  Note (fix): uses "python -m pip" / "python -m PyInstaller"
REM  instead of bare "pip"/"pyinstaller" - avoids PATH issues when
REM  pip does a "user install" (their .exe files land in a Scripts
REM  folder that isn't always on PATH).
REM
REM  Note (fix): "the terminal closed and I couldn't see what
REM  happened" - this script now ALWAYS writes a full log file
REM  (build_log.txt, next to this .bat) of everything it does,
REM  BEFORE showing/closing anything. It does this by re-launching
REM  itself once with output redirected to that file, then printing
REM  the whole log back to the screen and waiting for a keypress.
REM  This guarantees you can always see (and re-read afterward) the
REM  full output, even if something crashes hard or the window would
REM  otherwise have closed too fast to read.
REM ============================================================

if "%~1"=="__inner__" goto :inner

call "%~f0" __inner__ > "%~dp0build_log.txt" 2>&1
echo.
echo ================= build_log.txt ==================
type "%~dp0build_log.txt"
echo ===================================================
echo.
echo Full log saved to: %~dp0build_log.txt
echo (open it directly if the window is too small to see everything above)
echo.
pause
exit /b

:inner
REM Everything below runs with its output captured to build_log.txt -
REM no "pause" calls here (a pause would just hang invisibly, since
REM you can't see or respond to a prompt while output is redirected
REM to a file). The single pause that matters is in the outer block
REM above, AFTER the log has been printed back to the screen.

REM Make sure requirements.txt includes nvidia-cublas-cu12,
REM nvidia-cudnn-cu12 and ctranslate2>=4.0 (see
REM requirements-gpu-additions.txt) - otherwise the next line won't
REM install them, and --collect-all below will have nothing to collect.
echo Installing dependencies (if not already installed)...
python -m pip install -r requirements.txt
echo [pip exit code: %errorlevel%]

echo.
echo Building executable...
REM --collect-all on nvidia.cublas/nvidia.cudnn/ctranslate2 is
REM essential for GPU support to work for other people: these are DLL
REM packages only (not the full CUDA Toolkit) - without these flags
REM PyInstaller can't auto-detect them (they're not imported via a
REM normal "import" in the code, ctranslate2 loads the DLLs
REM dynamically at runtime), and GPU runs would fail for the end user
REM with something like "cublas64_12.dll not found", even with a
REM valid NVIDIA card. Note: the ivrit-ai/whisper-large-v3-ct2 model
REM itself is NOT bundled here via --add-data - it downloads
REM automatically from Hugging Face on first run and is cached locally
REM (%USERPROFILE%\.cache\huggingface).
REM
REM NOTE (fix): --collect-all faster_whisper is ALSO essential - the
REM faster_whisper package ships a bundled VAD (voice activity
REM detection) model file, faster_whisper/assets/silero_vad_v6.onnx,
REM as package DATA (not Python code), which PyInstaller does not
REM auto-detect for the same reason as the nvidia DLLs above. Without
REM this flag, the built .exe crashes on EVERY transcription attempt
REM with "onnxruntime...NoSuchFile: ...silero_vad_v6.onnx ... File
REM doesn't exist" - which looks like "the app doesn't hear me at all"
REM to the end user, since speech recognition fails silently in a loop.
REM
REM NOTE (fix): the --exclude-module flags below fix a huge (~1GB+)
REM size bloat that has nothing to do with anything Deus actually uses.
REM ctranslate2/__init__.py unconditionally does
REM "from ctranslate2 import converters" (a HuggingFace/Fairseq/Marian/
REM etc. model-CONVERSION tool - Deus only ever LOADS already-converted
REM models, never converts any), and
REM ctranslate2/converters/transformers.py does
REM "try: import torch; import transformers except ImportError: pass"
REM at module level. If your Python environment happens to have
REM torch/transformers installed (e.g. for other, unrelated ML/TTS
REM projects - torch alone is 1GB+), PyInstaller's static analysis has
REM no way to know that try/except makes them optional at runtime, so
REM it bundles all of torch/transformers (and whatever THEY pull in -
REM xformers, bitsandbytes, diffusers, pandas, pyarrow, scipy, numba...)
REM into the exe, even though nothing in Deus ever imports them.
REM Excluding them here is safe: the try/except in ctranslate2 already
REM handles ImportError gracefully at runtime, so ctranslate2 (and
REM faster-whisper) keep working normally without these installed in
REM the built exe - only the never-used conversion feature is gone.
REM
REM NOTE: --icon "assets\deus_icon.ico" - without this, PyInstaller
REM embeds its own generic default icon into Deus.exe. That icon shows
REM up everywhere Windows displays the exe without the app actually
REM running yet: the taskbar/Start Menu/Desktop shortcuts (installer.iss
REM doesn't override this - they inherit whatever icon is baked into the
REM exe itself), File Explorer, and Add/Remove Programs. deus_icon.ico
REM is generated from the first frame of assets\deus_idle.gif, cropped
REM tightly to the actual visible artwork (the raw GIF frame is mostly
REM empty transparent padding, which made the icon look tiny) - see the
REM matching runtime tray icon in ui\overlay_window.py.
python -m PyInstaller --noconfirm --onefile --windowed ^
    --name "Deus" ^
    --icon "assets\deus_icon.ico" ^
    --add-data "assets;assets" ^
    --add-data "config.json;." ^
    --collect-all nvidia.cublas ^
    --collect-all nvidia.cudnn ^
    --collect-all ctranslate2 ^
    --collect-all faster_whisper ^
    --exclude-module torch ^
    --exclude-module transformers ^
    --exclude-module diffusers ^
    --exclude-module bitsandbytes ^
    --exclude-module xformers ^
    --exclude-module datasets ^
    --exclude-module accelerate ^
    --exclude-module pyarrow ^
    --exclude-module pandas ^
    --exclude-module scipy ^
    --exclude-module numba ^
    --exclude-module llvmlite ^
    --exclude-module decord ^
    main.py
echo [PyInstaller exit code: %errorlevel%]

if exist "dist\Deus.exe" goto :exe_ok
echo.
echo ERROR: building dist\Deus.exe failed - see the output above
echo for the actual Python/PyInstaller error that caused this.
exit /b 1

:exe_ok
echo.
echo Executable ready: dist\Deus.exe

REM --- Try to locate the Inno Setup Compiler (ISCC.exe) and build an installer ---
REM NOTE (fix): this whole section is written with goto/labels instead
REM of multi-line "if ( ... ) else ( ... )" blocks on purpose. Two
REM earlier attempts to fix a parsing error here (isolating
REM %ProgramFiles(x86)%'s parentheses into a plain variable, then
REM fixing the file's line endings to CRLF) each fixed a real,
REM legitimate problem, but the error persisted regardless - meaning
REM multi-line parenthesized blocks in cmd.exe are just fragile in
REM general on some systems/setups. goto/labels avoid that whole
REM class of problem entirely: every branch below is a single plain
REM line, nothing is ever nested in parentheses, so there is nothing
REM left for the parser to misread.
set "PF86=%ProgramFiles(x86)%"
set "PF64=%ProgramFiles%"
set "ISCC="
if exist "%PF86%\Inno Setup 6\ISCC.exe" set "ISCC=%PF86%\Inno Setup 6\ISCC.exe"
if exist "%PF64%\Inno Setup 6\ISCC.exe" set "ISCC=%PF64%\Inno Setup 6\ISCC.exe"

if not defined ISCC goto :no_inno

echo.
echo Found Inno Setup at: %ISCC%
echo Building installer...
"%ISCC%" installer.iss
echo [ISCC exit code: %errorlevel%]
echo.
if exist "installer_output\DeusSetup.exe" goto :installer_ok

echo WARNING: ISCC ran but installer_output\DeusSetup.exe was not
echo created - check the ISCC output above for the actual error
echo (for example a missing/misnamed file referenced in installer.iss).
goto :end_script

:installer_ok
echo Done! The installer is at: installer_output\DeusSetup.exe
echo You can hand DeusSetup.exe to anyone - it's a normal install
echo with shortcuts and an uninstaller, nothing to explain.
goto :end_script

:no_inno
echo.
echo Inno Setup was NOT found in the usual Program Files locations,
echo so no full installer was built this time.
echo You can still hand out dist\Deus.exe by itself and run it
echo directly. To also build a real installer (DeusSetup.exe):
echo install the free Inno Setup from https://jrsoftware.org/isdl.php
echo and then run this script (build_exe.bat) again.

:end_script
exit /b 0
