"""CLI 工具入口（T9-1~T9-6）。

基于 Typer 的命令行工具，提供：
- init-cases: 初始化评测用例（T9-2）
- eval run: 运行评测（T9-3）
- eval status: 查看评测状态（T9-4）
- backup: 备份数据库（T9-5）
- cleanup: 清理临时文件（T9-6）

使用方式：
    python -m app.cli.main init-cases
    python -m app.cli.main eval run --name "测试运行"
    python -m app.cli.main eval status <run_id>
    python -m app.cli.main backup
    python -m app.cli.main cleanup
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import typer

from app.config import get_config, get_config_paths
from app.core.logging import get_logger

app = typer.Typer(
    name="mae-eval",
    help="多智能体编排与模型评测一体化平台 CLI",
    no_args_is_help=True,
)

eval_app = typer.Typer(help="评测管理命令", no_args_is_help=True)
app.add_typer(eval_app, name="eval")

logger = get_logger(__name__)
config = get_config()


@app.command("init-cases")
def init_cases(
    force: bool = typer.Option(False, "--force", "-f", help="强制重新导入"),
) -> None:
    """初始化评测用例（T9-2）。

    从 data/eval_cases/cases.yaml 导入用例到数据库。
    幂等导入，已存在的用例跳过。
    """
    import asyncio

    from app.eval.loader import EvalCaseLoader

    typer.echo("正在加载评测用例...")
    loader = EvalCaseLoader()

    try:
        cases = loader.load_from_yaml()
        typer.echo(f"YAML 文件中找到 {len(cases)} 条用例")

        # 校验
        errors = loader.validate_cases(cases)
        if errors:
            typer.secho(f"发现 {len(errors)} 个校验问题：", fg=typer.colors.YELLOW)
            for err in errors[:10]:
                typer.echo(f"  - {err}")
            if len(errors) > 10:
                typer.echo(f"  ... 还有 {len(errors) - 10} 个问题")

        # 导入
        imported, total = asyncio.run(loader.import_to_db(cases))
        typer.secho(
            f"导入完成：新增 {imported} 条，总计 {total} 条",
            fg=typer.colors.GREEN,
        )
    except Exception as e:
        typer.secho(f"导入失败: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@eval_app.command("run")
def eval_run(
    name: str = typer.Option(..., "--name", "-n", help="评测运行名称"),
    category: str = typer.Option(None, "--category", "-c", help="按类别筛选"),
    model: str = typer.Option("deepseek-chat", "--model", "-m", help="模型名称"),
) -> None:
    """运行评测（T9-3）。

    创建评测运行并开始执行用例。
    """
    import asyncio

    from app.eval.eval_service import EvalService

    typer.echo(f"创建评测运行: {name}")
    service = EvalService()

    try:
        run = asyncio.run(service.create_run(
            name=name,
            model_config={"model": model},
            category=category,
        ))
        typer.secho(f"评测运行已创建: {run.id}", fg=typer.colors.GREEN)
        typer.echo(f"  用例总数: {run.total_cases}")
        typer.echo(f"  状态: {run.status}")
        typer.echo(f"  查看状态: python -m app.cli.main eval status {run.id}")
    except Exception as e:
        typer.secho(f"创建失败: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@eval_app.command("status")
def eval_status(
    run_id: str = typer.Argument(..., help="评测运行 ID"),
) -> None:
    """查看评测运行状态（T9-4）。"""
    import asyncio

    from app.eval.eval_service import EvalService

    service = EvalService()
    try:
        run = asyncio.run(service.get_run(run_id))
        typer.echo(f"运行 ID: {run.id}")
        typer.echo(f"名称: {run.name}")
        typer.echo(f"状态: {run.status}")
        typer.echo(f"进度: {run.completed_cases}/{run.total_cases} "
                   f"({run.completed_cases/run.total_cases*100:.1f}%)" if run.total_cases else "进度: 0")
        typer.echo(f"通过: {run.passed_cases}")
        if run.avg_score is not None:
            typer.echo(f"平均分: {run.avg_score}")
    except Exception as e:
        typer.secho(f"查询失败: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command("backup")
def backup(
    output: Path = typer.Option(None, "--output", "-o", help="备份文件路径"),
) -> None:
    """备份数据库（T9-5）。

    将业务数据库复制到备份文件。
    """
    paths = get_config_paths()
    db_path = paths.get("db", Path("data/db/app.db"))

    if output is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output = Path("data/backups") / f"app_backup_{timestamp}.db"

    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        shutil.copy2(db_path, output)
        typer.secho(f"备份成功: {output}", fg=typer.colors.GREEN)
    except Exception as e:
        typer.secho(f"备份失败: {e}", fg=typer.colors.RED)
        raise typer.Exit(code=1)


@app.command("cleanup")
def cleanup(
    dry_run: bool = typer.Option(False, "--dry-run", help="仅显示将清理的文件，不实际删除"),
) -> None:
    """清理临时文件（T9-6）。

    清理：临时检查点、过期报告缓存、__pycache__。
    """
    paths = get_config_paths()
    cleaned = []

    # 清理 __pycache__
    for pycache in Path(".").rglob("__pycache__"):
        if dry_run:
            cleaned.append(str(pycache))
        else:
            shutil.rmtree(pycache, ignore_errors=True)
            cleaned.append(str(pycache))

    # 清理临时检查点（保留最近 10 个任务的）
    checkpoint_dir = paths.get("checkpoints", Path("data/checkpoints"))
    if checkpoint_dir.exists():
        task_dirs = sorted([d for d in checkpoint_dir.iterdir() if d.is_dir()],
                           key=lambda x: x.stat().st_mtime, reverse=True)
        for old_dir in task_dirs[10:]:
            if dry_run:
                cleaned.append(str(old_dir))
            else:
                shutil.rmtree(old_dir, ignore_errors=True)
                cleaned.append(str(old_dir))

    if dry_run:
        typer.echo(f"将清理 {len(cleaned)} 项（dry-run 模式）:")
    else:
        typer.secho(f"已清理 {len(cleaned)} 项", fg=typer.colors.GREEN)

    for item in cleaned[:20]:
        typer.echo(f"  - {item}")
    if len(cleaned) > 20:
        typer.echo(f"  ... 还有 {len(cleaned) - 20} 项")


def main() -> None:
    """CLI 入口函数。"""
    app()


if __name__ == "__main__":
    main()
