# <img src="scripts/site/hinnom.svg" alt="hinnom" width="332">

Void Linux package repository.

[Browse packages](https://hinnom.znx.pw/)

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

## Building

```sh
scripts/build [package...]
scripts/check-updates
scripts/check-compat
```

`check-updates` exits with 0 when packages are current, 1 when updates are
available, or 2 when any lookup fails.

`check-compat` checks the published packages against Void's current
repositories. It reports shared libraries and dependencies that no package
provides any more, and Void packages that still need a library replaced by one
of these templates. It exits with 0 when everything fits, 1 when a package needs
a rebuild, or 2 when a repository cannot be read.
