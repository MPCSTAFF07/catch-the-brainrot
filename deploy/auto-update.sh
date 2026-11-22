#!/usr/bin/env bash
set -euo pipefail

repo=/home/ubuntu/catch-the-brainrot
state=/var/lib/catch-the-brainrot-deploy
service=catch-the-brainrot.service

as_ubuntu() {
    runuser -u ubuntu -- "$@"
}

repo_git() {
    as_ubuntu git -C "$repo" "$@"
}

if [[ $(repo_git symbolic-ref --quiet --short HEAD) != main ]]; then
    echo "自動更新にはEC2の作業コピーで main を使用してください。" >&2
    exit 1
fi

repo_git fetch --quiet --no-tags origin main
target=$(repo_git rev-parse --verify 'FETCH_HEAD^{commit}')
head=$(repo_git rev-parse --verify HEAD)
deployed=""
if [[ -f $state/revision ]]; then
    read -r deployed < "$state/revision"
fi
if [[ $head == "$target" && $deployed == "$target" ]]; then
    exit 0
fi

if [[ -n $(repo_git status --porcelain --untracked-files=no) ]]; then
    echo "EC2に未コミットの変更があるため、自動更新を中止しました。" >&2
    exit 1
fi
if ! repo_git merge-base --is-ancestor "$head" "$target"; then
    echo "EC2のmainとGitHubのmainが分岐しているため、自動更新を中止しました。" >&2
    exit 1
fi

echo "GitHubの更新を適用します: $target"
if [[ $head != "$target" ]]; then
    repo_git merge --ff-only "$target"
fi
if [[ -z $deployed ]] ||
   ! repo_git cat-file -e "$deployed^{commit}" ||
   ! repo_git diff --quiet "$deployed" "$target" -- requirements.txt ||
   [[ ! -x $repo/.venv/bin/python ]]; then
    if [[ ! -x $repo/.venv/bin/python ]]; then
        as_ubuntu python3 -m venv "$repo/.venv"
    fi
    as_ubuntu "$repo/.venv/bin/python" -m pip install --disable-pip-version-check -r "$repo/requirements.txt"
fi

systemctl restart "$service"
for attempt in {1..15}; do
    status=$(curl --silent --max-time 2 --output /dev/null --write-out '%{http_code}' \
        -H 'Host: catch-the-brainrot.eggkingdom.com' \
        -H 'X-Forwarded-Proto: https' http://127.0.0.1:18791/login) || status=
    if [[ $status == 200 ]] && systemctl is-active --quiet "$service"; then
        echo "$target" > "$state/revision.tmp"
        mv -f "$state/revision.tmp" "$state/revision"
        echo "自動更新完了: $target"
        exit 0
    fi
    sleep 1
done

echo "起動後にログイン画面を確認できませんでした。$service のログを確認してください。" >&2
exit 1
