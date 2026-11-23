# Installing and Building FoamCutter

Use this guide if you are a maintainer or teammate building FoamCutter from source.

End users should download the matching binary package from the project's Releases page when one is available. They do not need Python, Git, a virtual environment, or a terminal.

## Platform rule

PyInstaller builds only for the operating system on which it is run:

- Build on Windows for a Windows executable.
- Build on macOS for a macOS application.
- Build on Ubuntu/Linux for a Linux executable.

Windows, macOS, and Linux binaries are not interchangeable. Do not rename a binary for another platform.

## Download and build a release

### 1. Install Git

Download Git for your platform if it is not already installed:

- Windows: https://git-scm.com/download/win
- macOS: https://git-scm.com/download/mac
- Ubuntu: `sudo apt install git`

Open Git Bash on Windows, Terminal on macOS, or Terminal on Ubuntu.

### 2. Clone the project

```bash
git clone <repository-url>
cd foamcutter
```

Replace `<repository-url>` with the URL of this project's Git repository.

### 3. Install the platform prerequisites

#### Windows

Install Python 3.11 or newer from https://www.python.org/downloads/windows/. Enable **Add python.exe to PATH** during installation.

Verify:

```powershell
python --version
```

#### macOS

Install Python 3.11 or newer, for example from https://www.python.org/downloads/macos/. Xcode Command Line Tools may be required:

```bash
xcode-select --install
python3 --version
```

#### Ubuntu

```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip python3-tk
python3 --version
```

### 4. Build

Build with the repository script. It creates an isolated `.build-venv`, installs the project dependencies and PyInstaller, and produces the binary.

#### Windows

In Git Bash or PowerShell:

```powershell
python build_app.py
```

Output:

```text
dist/FoamCutter/FoamCutter.exe
```

The distributable is the complete `dist/FoamCutter` folder. ZIP that folder before uploading it to the release:

```powershell
Compress-Archive -Path dist/FoamCutter -DestinationPath FoamCutter-Windows.zip
```

#### macOS

```bash
python3 build_app.py
```

Output:

```text
dist/FoamCutter.app
```

Create a release archive:

```bash
ditto -c -k --sequesterRsrc --keepParent dist/FoamCutter.app FoamCutter-macOS.zip
```

macOS may later require an Apple Developer ID signature and notarization for a polished release. Unsigned applications can trigger a security warning.

#### Ubuntu/Linux

```bash
python3 build_app.py
```

Output:

```text
dist/FoamCutter/FoamCutter
```

Create a release archive containing the whole application directory:

```bash
tar -C dist -czf FoamCutter-Ubuntu.tar.gz FoamCutter
```

Test it before uploading:

```bash
chmod +x dist/FoamCutter/FoamCutter
./dist/FoamCutter/FoamCutter
```

## Publishing platform binaries

A maintainer on each platform can build and upload the package to the GitHub Release:

- Windows maintainer: upload `FoamCutter-Windows.zip`
- macOS maintainer: upload `FoamCutter-macOS.zip`
- Ubuntu maintainer: upload `FoamCutter-Ubuntu.tar.gz`

For a source commit, release the outputs together using clear names and SHA-256 checksums. Every user can then download the package for their own platform and run it without compiling or configuring Python.

Windows and macOS team partners do not need to add build programs to the repository. They should run `build_app.py` on their own native operating system and upload only the generated platform package to the release.

## Installing an already-built release

### Windows

1. Download `FoamCutter-Windows.zip`.
2. Extract the ZIP.
3. Open the extracted `FoamCutter` folder.
4. Run `FoamCutter.exe`.

Keep the executable and its neighboring files together.

### macOS

1. Download `FoamCutter-macOS.zip`.
2. Unzip it.
3. Open `FoamCutter.app`.
4. If macOS blocks an unsigned application, use **System Settings > Privacy & Security**, or verify/sign it properly before release.

### Ubuntu/Linux

1. Download `FoamCutter-Ubuntu.tar.gz`.
2. Extract it:

```bash
tar -xzf FoamCutter-Ubuntu.tar.gz
```

3. Run it:

```bash
cd FoamCutter
./FoamCutter
```

## Troubleshooting builds

- `python is not recognized`: reopen the terminal after installing Python, or use `python3` on macOS/Linux.
- `PyInstaller cannot find a module`: make sure `build_app.py` is run from the repository root.
- `No module named ...`: remove `.build-venv` and run the build script again.
- macOS reports that the app is damaged or unidentified: use a signed/notarized build for broad distribution.
- Ubuntu fails with a missing library: test against the Ubuntu version used by the intended users.

Do not move or edit files inside `build/` or `dist/`. Those directories are generated build output.

