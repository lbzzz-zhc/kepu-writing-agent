#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
用 GitHub REST API 推送仓库内容（当 `git push` 因网络阻断不可用时）。

背景：
  部分网络环境下 github.com:443 不可达，但 api.github.com 可达 —— 此时
  `git push` 一定失败，而本脚本仍能完成推送。

用法：
  python push_to_github.py --repo <owner>/<name> [--branch main] [--message "..."]
  python push_to_github.py --repo lbzzz-zhc/kepu-writing-agent
  python push_to_github.py --repo ... --base-branch main --dry-run

鉴权：优先读环境变量 GITHUB_TOKEN；否则调用 `gh auth token`。

行为：
  · 以工作目录下 git 已跟踪的文件为准（git ls-files），不推送被忽略的文件
  · 若远端分支已存在，则在其之上追加一个新提交（保留历史，不强制覆盖）
  · 若分支不存在，则创建（此时无父提交）
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"


def local_blob_sha(data):
    """git blob 的 SHA-1（与 git hash-object 一致），用于跳过未改动文件。"""
    import hashlib
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def remote_tree(gh, repo, tree_sha):
    """拉取远端 tree，递归展开为 {path: blob_sha}。"""
    if not tree_sha:
        return {}
    _, data = gh.call("GET", f"/repos/{repo}/git/trees/{tree_sha}?recursive=1")
    return {e["path"]: e["sha"] for e in data.get("tree", []) if e.get("type") == "blob"}


def token():
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        print("[失败] 拿不到 GitHub token：请设置 GITHUB_TOKEN，或先 `gh auth login`", file=sys.stderr)
        sys.exit(1)


class GH:
    def __init__(self, tok):
        self.tok = tok

    def call(self, method, path, payload=None, ok=(200, 201, 204)):
        url = API + path
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": "Bearer " + self.tok,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "kepu-writer-push",
            "Content-Type": "application/json",
        })
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            return resp.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            if exc.code in ok:
                return exc.code, {}
            print(f"[失败] {method} {path} → {exc.code}\n        {detail[:400]}", file=sys.stderr)
            sys.exit(1)
        except Exception as exc:  # noqa: BLE001
            print(f"[失败] {method} {path} 网络异常：{exc}", file=sys.stderr)
            sys.exit(1)


def tracked_files():
    try:
        out = subprocess.run(["git", "ls-files", "-z"], capture_output=True, check=True)
    except Exception:  # noqa: BLE001
        print("[失败] 当前目录不是 git 仓库（或没有 git）", file=sys.stderr)
        sys.exit(1)
    names = [n for n in out.stdout.decode("utf-8", "replace").split("\0") if n]
    if not names:
        print("[失败] git 没有跟踪任何文件，先 `git add`", file=sys.stderr)
        sys.exit(1)
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="owner/name")
    ap.add_argument("--branch", default="main")
    ap.add_argument("--message", default="")
    ap.add_argument("--root", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    gh = GH(token())
    files = tracked_files()
    print(f"仓库：{args.repo}   分支：{args.branch}   文件：{len(files)} 个")

    # 当前分支头（若存在则作为父提交，保留历史）
    # 注意：空仓库返回 409 "Git Repository is empty."，与 404 同等对待
    parent = None
    status, ref = gh.call("GET", f"/repos/{args.repo}/git/ref/heads/{args.branch}", ok=(200, 404, 409))
    if status == 200 and ref.get("object"):
        parent = ref["object"]["sha"]
        print(f"远端已有分支，父提交：{parent[:8]}")
    else:
        print("远端分支不存在，将创建首个提交")

    # 空仓库需要用 Contents API 先建一个提交，Git Data API 才可用
    if parent is None:
        _, repo_info = gh.call("GET", f"/repos/{args.repo}")
        default_branch = repo_info.get("default_branch") or "main"
        seed_name = "README.md" if os.path.exists(os.path.join(args.root, "README.md")) else ".gitkeep"
        seed_path = os.path.join(args.root, seed_name)
        seed = open(seed_path, "rb").read() if os.path.exists(seed_path) else b"# init\n"
        print(f"空仓库：先用 {seed_name} 初始化（默认分支 {default_branch}）")
        gh.call("PUT", f"/repos/{args.repo}/contents/{seed_name}", {
            "message": "chore: 初始化仓库",
            "content": base64.b64encode(seed).decode("ascii"),
        })
        status, ref = gh.call("GET", f"/repos/{args.repo}/git/ref/heads/{default_branch}",
                              ok=(200, 404, 409))
        if status == 200:
            parent = ref["object"]["sha"]
            if default_branch != args.branch:
                print(f"[提示] 默认分支是 {default_branch}，请改用 --branch {default_branch} 保持一致",
                      file=sys.stderr)

    if args.dry_run:
        print("\n[dry-run] 只列出将推送的文件：")
        for f in files:
            print("  " + f)
        return 0

    # 1) 增量上传 blobs：内容未变的文件复用远端已有 blob，避免重复上传与中途断连
    remote = {}
    base_tree_sha = None
    if parent:
        _, head_commit = gh.call("GET", f"/repos/{args.repo}/git/commits/{parent}")
        base_tree_sha = head_commit.get("tree", {}).get("sha")
        remote = remote_tree(gh, args.repo, base_tree_sha)
        print(f"远端已有 {len(remote)} 个文件，将只上传有改动的")

    tree, reused, uploaded = [], 0, 0
    for i, rel in enumerate(files, 1):
        path = os.path.join(args.root, rel)
        with open(path, "rb") as fh:
            raw = fh.read()
        key = rel.replace(os.sep, "/")
        sha = local_blob_sha(raw)
        if remote.get(key) == sha:
            tree.append({"path": key, "mode": "100644", "type": "blob", "sha": sha})
            reused += 1
            continue
        for attempt in range(1, 4):
            try:
                _, blob = gh.call("POST", f"/repos/{args.repo}/git/blobs",
                                  {"content": base64.b64encode(raw).decode("ascii"),
                                   "encoding": "base64"})
                break
            except SystemExit:
                if attempt == 3:
                    raise
                print(f"  [重试 {attempt}/3] {key}", file=sys.stderr)
                time.sleep(2 * attempt)
        tree.append({"path": key, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        uploaded += 1
        if uploaded % 10 == 0:
            print(f"  已上传 {uploaded} 个（复用 {reused} 个）")
    print(f"blob 处理完成：新上传 {uploaded} 个，复用 {reused} 个")

    # 2) 建 tree
    payload = {"tree": tree}
    if parent:
        payload["base_tree"] = base_tree_sha
    _, new_tree = gh.call("POST", f"/repos/{args.repo}/git/trees", payload)
    print(f"tree 已创建：{new_tree['sha'][:8]}")

    # 3) 建 commit
    msg = args.message or "通过 API 推送更新"
    commit_payload = {"message": msg, "tree": new_tree["sha"]}
    if parent:
        commit_payload["parents"] = [parent]
    _, commit = gh.call("POST", f"/repos/{args.repo}/git/commits", commit_payload)
    print(f"commit 已创建：{commit['sha'][:8]}  {msg.splitlines()[0][:50]}")

    # 4) 更新或创建 ref
    if parent:
        gh.call("PATCH", f"/repos/{args.repo}/git/refs/heads/{args.branch}",
                {"sha": commit["sha"], "force": False})
    else:
        gh.call("POST", f"/repos/{args.repo}/git/refs",
                {"ref": f"refs/heads/{args.branch}", "sha": commit["sha"]})
    print(f"分支已更新：{args.branch} → {commit['sha'][:8]}")
    print(f"仓库地址：https://github.com/{args.repo}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
