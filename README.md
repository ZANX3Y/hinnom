# hinnom

Void Linux package repository.

## Installation

Add the repository:

```sh
echo 'repository=https://hinnom.znx.pw/x86_64' | sudo tee /etc/xbps.d/00-hinnom.conf
```

Sync and accept the signing key:

```sh
sudo xbps-install -S
```

Install packages:

```sh
sudo xbps-install <package>
```

## Packages

- aquamarine
- better-adb-sync
- brave-origin
- darkly
- ddev
- desktop-plus
- equibop
- font-google-sans-flex
- glaze
- helium-browser
- hyprcursor
- hyprgraphics
- hypridle
- hyprland
- hyprland-guiutils
- hyprland-protocols
- hyprlang
- hyprlock
- hyprpaper
- hyprpicker
- hyprpolkitagent
- hyprshutdown
- hyprsunset
- hyprtoolkit
- hyprutils
- hyprwayland-scanner
- hyprwire
- mkcert
- popsicle
- spotify-launcher
- xdg-desktop-portal-hyprland

## Building

```sh
scripts/build [package...]
scripts/check-updates
```

`check-updates` exits with 0 when packages are current, 1 when updates are
available, or 2 when any lookup fails.
