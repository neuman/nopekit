# Installing the tools

`fw.ci_job` needs Python and PyYAML (`pip install pyyaml`). `fw.build`,
`fw.image_fits`, `fw.sleep_wake` and `fw.tx_power` need `act`, `docker` and `git` on
PATH, and the images the job runs in already pulled. Without them they read Skipped,
never passed.

Linux with docker only. Your user must be in the `docker` group. No step needs
sudo. macOS and Windows are untested.

## 1. act v0.2.97

The gates run act v0.2.97 exactly. Another version reads Skipped and names this
step: a flag's meaning or the shape of act's JSON log can change between releases.

```sh
mkdir -p ~/.local/share/act-v0.2.97 && cd ~/.local/share/act-v0.2.97
curl -L -o act_Linux_x86_64.tar.gz https://github.com/nektos/act/releases/download/v0.2.97/act_Linux_x86_64.tar.gz
echo "67b4976a56f33ea7870b798912e738ec44eef38e7578ae7b532f2def1c73a5ad  act_Linux_x86_64.tar.gz" | sha256sum -c -
tar -xzf act_Linux_x86_64.tar.gz act
ln -s ~/.local/share/act-v0.2.97/act ~/.local/bin/act
act --version
```

The sha256 is the one the release's own `checksums.txt` lists for
`act_Linux_x86_64.tar.gz`. `act --version` must print `act version 0.2.97`.

## 2. The images

The replay runs with `--pull=false`: it never fetches an image, and a missing one is
errored and named. Pull each image your job runs in. For this pack's own baseline:

```sh
docker pull espressif/idf@sha256:bf72fe7a15c22849a9a94147301165a545fb8905d97c176cf748e3e4614cac84
```

That is ESP-IDF v5.5.6, about 5.4 GB. A job with no `container:` runs in a runner
image instead; the pack maps `ubuntu-latest` and `ubuntu-24.04` to
`catthehacker/ubuntu@sha256:c58e2b364da03b0c804c7d660f2ecbedf2f221a382b9baa0b344b0144780ff43`
(act-24.04-20260815) and `ubuntu-22.04` to
`catthehacker/ubuntu@sha256:3488f78aa97770c8d5d835f2913c1a782d3121ad3f2aafb17054fb1d554ea4e1`
(act-22.04-20260815). Pull the one your job needs.

A job that uses a JavaScript action (`actions/upload-artifact`, `actions/cache`)
needs `node` in its image. espressif/idf and zmk-build-arm have none, so such a step
fails under act. A step after the build is named in the detail and not judged; a step
before it fails the replay as errored. act fetches each action once into
`~/.cache/nopekit/firmware-ci/actions` and reuses it after (`--action-offline-mode`).

## 3. A smoke test

```sh
PYTHONPATH=src python3 -m nopekit gate selftest --pack firmware-ci
```

The path is relative to a nopekit checkout. This replays the pack's baseline and its
four known-bad copies that reach the build. Each of the five gates must say its
known-good passed and its known-bad failed. One replay took 40-49 s on seven cores;
`pack validate` runs fifteen of them (counted on 2026-11-24, about 20 min on a
loaded machine).

## What the replay does

For the chosen job, act runs `push` on a copy of what the next commit would hold:
`git ls-files -co --exclude-standard` in a work tree, or every file under the
repository where it is ignored or in no work tree, without `.git`, `.nopekit` and
act's `.actrc`, `.secrets`, `.env`, `.vars` and `.input`. The copy gets one commit
with a fixed author and date, because act reads the commit and the branch from git.
act runs with `--bind` (the job writes into the copy), `--container-daemon-socket -`
(no docker socket in any job), `/dev/null` for secrets, variables, environment and
inputs, no cache server, and HOME pointed at an empty directory, so no `.actrc`
applies. After the replay the copy is handed back to your user by a `chown` in a
container of the job's own image, and removed.
