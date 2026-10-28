#!/usr/bin/env python
"""生成演示数据：知识库样例文档 + Agent 工作区样例文件。

用法：
    python -m scripts.seed_demo            # 只生成文件
    python -m scripts.seed_demo --ingest   # 生成文件并尝试写入向量库（需要 Ollama）

为什么需要它：
    仓库不提交 data/ 与向量库文件（体积大且可能含用户数据），
    所以陌生人 clone 下来是「空库」。这个脚本让 demo 环境 30 秒可用。
"""

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

KNOWLEDGE_DOC = """扫地机器人售后知识库（演示用）

一、日常保养
1. 滤网建议每 2 周清洗一次，每 3~6 个月更换一次；清洗后需完全晾干再装回，否则会触发风道堵塞告警。
2. 边刷缠绕毛发时，请关机后取下边刷清理，避免电机过载。
3. 主刷（滚刷）每 1 个月检查一次轴承处毛发，长期不清理会导致异响与吸力下降。
4. 尘盒容量约 0.4L，建议每次清扫后倾倒；水箱加水不要超过 MAX 刻度线。

二、常见故障代码
1. E1：电池电量过低，请将设备放回充电座充电 4 小时以上。
2. E3：风道堵塞，多为滤网积尘或尘盒未装好，请清理滤网并确认尘盒卡扣到位。
3. E5：边刷缠绕异物，请关机后清理边刷。
4. E9：悬崖传感器被遮挡或脏污，请用干布擦拭底部传感器窗口。

三、硬件配置
1. 搭载 AI 视觉芯片，配合 LDS 激光雷达实现 SLAM 建图与自主导航。
2. 底部配备 4 组悬崖传感器、1 组沿墙传感器，碰撞板内置机械微动开关。
3. 电池为 5200mAh 锂电池，标准模式续航约 150 分钟。

四、保修政策
1. 整机保修 2 年，电池保修 1 年，耗材（滤网、边刷、滚刷）不保修。
2. 保修期自激活日期起算，需提供设备 SN 码；SN 码可在机身底部或 App「设备信息」中查看。
3. 人为进水、摔碰、自行拆机导致的故障不在保修范围内。
"""

WORKSPACE_FILES = {
    "device.txt": (
        "SN1001\n"
        "型号: RoboVac X10 Pro\n"
        "激活日期: 2025-01-10\n"
        "故障现象: 清扫 10 分钟后报 E3，吸力明显下降\n"
    ),
    "test.txt": "这是一个用于验证 MCP read_file / write_file 工具的样例文件。\n",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成演示数据")
    parser.add_argument("--ingest", action="store_true", help="生成后尝试写入向量库（需要 Ollama）")
    args = parser.parse_args()

    knowledge_dir = REPO_ROOT / "data" / "knowledge"
    knowledge_dir.mkdir(parents=True, exist_ok=True)
    doc_path = knowledge_dir / "售后手册_扫地机器人.txt"
    doc_path.write_text(KNOWLEDGE_DOC, encoding="utf-8")
    print(f"✅ 知识库样例: {doc_path.relative_to(REPO_ROOT)}")

    workspace = Path(REPO_ROOT / "Agent" / "mcp_tools" / "workspace")
    workspace.mkdir(parents=True, exist_ok=True)
    for name, content in WORKSPACE_FILES.items():
        target = workspace / name
        target.write_text(content, encoding="utf-8")
        print(f"✅ 工作区样例: {target.relative_to(REPO_ROOT)}")

    if not args.ingest:
        print("\n下一步（可选）：")
        print("  python -m scripts.seed_demo --ingest   # 需要 Ollama 已启动并拉好 embedding 模型")
        return 0

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        from RAG.RAG import KnowledgeBaseService

        service = KnowledgeBaseService(file_path=str(doc_path))
        print(service.upload())
    except Exception as exc:
        print(f"❌ 入库失败（不影响文件生成）: {exc}")
        print("   请确认 Ollama 已启动且已拉取 embedding 模型：ollama pull qwen3-embedding:4b")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
