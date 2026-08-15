#!/usr/bin/env python3
"""test_mirror_sync.py — scripts/ops 仓库镜像与 ~/.omp/guardian 生产副本的一致性门禁。

背景（2026-08-15）：proxies-supervisor.py / start-proxies-supervisor.bat / start.bat
三个生产看护文件长期未镜像入仓，apply-secrets-restart.ps1 新建后也只有生产副本；
watchdog.ps1 曾出现 CRLF-only 漂移。漂移的后果：事故复盘拿到的"仓库镜像"不是
实际运行版本（2026-08-15 agentrouter 8788 绑定事故即踩中）。

门禁语义：镜像必须**逐字节一致**（含 BOM/行尾）。ps1 依赖 BOM（PS 5.1 ANSI
解析坑），bat 行尾影响 cmd 解析，不容忍规范化差异。

生产目录缺失时跳过（CI/他机无 ~/.omp/guardian），避免误红。
"""
from __future__ import annotations

import unittest
from pathlib import Path

LIVE_DIR = Path.home() / ".omp" / "guardian"
REPO_DIR = Path(__file__).resolve().parent

MIRRORED = [
    "guardian.py",
    "proxies-supervisor.py",
    "watchdog.ps1",
    "start-proxies-supervisor.bat",
    "start.bat",
    "apply-secrets-restart.ps1",
    "anyrouter-window-canary.py",
]


@unittest.skipUnless(LIVE_DIR.is_dir(), "生产目录 ~/.omp/guardian 不存在（非生产机）")
class MirrorSyncTests(unittest.TestCase):
    def test_mirrored_files_are_byte_identical(self) -> None:
        for name in MIRRORED:
            live = LIVE_DIR / name
            repo = REPO_DIR / name
            with self.subTest(file=name):
                self.assertTrue(live.is_file(), f"生产副本缺失: {live}")
                self.assertTrue(repo.is_file(), f"仓库镜像缺失: {repo}（先镜像再提交）")
                self.assertEqual(
                    repo.read_bytes(),
                    live.read_bytes(),
                    f"{name} 镜像漂移：仓库与生产不一致（含 BOM/行尾）",
                )


if __name__ == "__main__":
    unittest.main()
