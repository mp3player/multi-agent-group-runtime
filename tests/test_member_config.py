import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from application import builders as app_builders
from application.config import AppConfig, MemberRuntimeConfig
from application.group_service import GroupAppService
from core.member_config import (
    MemberConfig,
    load_member_configs,
    merge_member_configs,
    save_member_configs,
)
from web_server import WebState
from tools.registry import ToolRegistry


class DummyAgent:
    def __init__(self) -> None:
        self.registry = ToolRegistry()
        self.system_builder = None
        self.llm = type("DummyLLM", (), {"model": "dummy", "base_url": "http://dummy"})()

    def rebuild_system_prompt(self) -> None:
        return None

    def reset_active_to_system(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


def test_addmember_command_accepts_description() -> None:
    old_build_agent = app_builders.build_agent
    app_builders.build_agent = lambda *args, **kwargs: DummyAgent()  # type: ignore[assignment]
    try:
        service = GroupAppService(
            group_name="review",
            member_configs=[],
            member_config_path=None,
            max_turns=1,
            enable_tools=True,
            group_max_rounds=5,
            persist_dynamic_members=False,
        )
        member = service.add_member_from_command(
            'Dev-A "technical reviewer focused on concurrency"'
        )

        assert member.name == "Dev-A"
        assert member.description == "technical reviewer focused on concurrency"
    finally:
        app_builders.build_agent = old_build_agent


def test_addmember_command_accepts_member_llm_options() -> None:
    parsed = GroupAppService(
        group_name="review",
        member_configs=[],
        member_config_path=None,
        max_turns=1,
        enable_tools=True,
        group_max_rounds=5,
        persist_dynamic_members=False,
    ).add_member_from_command

    old_build_agent = app_builders.build_agent
    seen_kwargs = []
    app_builders.build_agent = (  # type: ignore[assignment]
        lambda *args, **kwargs: seen_kwargs.append(kwargs) or DummyAgent()
    )
    try:
        member = parsed(
            'Dev-A "technical reviewer" '
            "--url https://dev.example/v1 --key dev-key --model dev-model"
        )

        assert member.name == "Dev-A"
        assert member.description == "technical reviewer"
        assert seen_kwargs[-1]["llm_config"].base_url == "https://dev.example/v1"
        assert seen_kwargs[-1]["llm_config"].api_key == "dev-key"
        assert seen_kwargs[-1]["llm_config"].model == "dev-model"
    finally:
        app_builders.build_agent = old_build_agent


def test_member_config_load_save_merge(tmp_path: Path) -> None:
    path = tmp_path / "members.json"
    members = [
        MemberConfig("Leader", "coordinates work"),
        MemberConfig(
            "Dev",
            "implements changes",
            base_url="https://dev.example/v1",
            api_key="dev-key",
            model="dev-model",
        ),
    ]

    save_member_configs(members, path)

    loaded = load_member_configs(path)
    assert loaded == members
    assert merge_member_configs(loaded, ["Dev", "Production"]) == [
        MemberConfig("Leader", "coordinates work"),
        MemberConfig(
            "Dev",
            "implements changes",
            base_url="https://dev.example/v1",
            api_key="dev-key",
            model="dev-model",
        ),
        MemberConfig("Production", ""),
    ]


def test_member_config_loads_url_key_aliases(tmp_path: Path) -> None:
    path = tmp_path / "members.json"
    path.write_text(
        '{"members":[{"name":"Dev","url":"https://dev.example/v1",'
        '"key":"dev-key","model":"dev-model"}]}',
        encoding="utf-8",
    )

    assert load_member_configs(path) == [
        MemberConfig(
            "Dev",
            "",
            base_url="https://dev.example/v1",
            api_key="dev-key",
            model="dev-model",
        )
    ]


def test_build_group_accepts_member_configs() -> None:
    old_build_agent = app_builders.build_agent
    app_builders.build_agent = lambda *args, **kwargs: DummyAgent()  # type: ignore[assignment]
    try:
        group = app_builders.build_group(
            AppConfig()
            .with_agent_options(max_turns=1, enable_tools=True)
            .with_group_options(name="review", max_dispatch_rounds=5)
            .with_members((
                MemberRuntimeConfig("Leader", "coordinates work"),
            )),
        )

        assert list(group.members) == ["Leader"]
        assert group.members["Leader"].description == "coordinates work"
    finally:
        app_builders.build_agent = old_build_agent


def test_build_group_reads_dispatch_policy_from_env() -> None:
    old_build_agent = app_builders.build_agent
    old_policy = os.environ.get("MAS_GROUP_DISPATCH_POLICY")
    app_builders.build_agent = lambda *args, **kwargs: DummyAgent()  # type: ignore[assignment]
    os.environ["MAS_GROUP_DISPATCH_POLICY"] = "on_demand"
    try:
        service = GroupAppService(
            group_name="review",
            member_configs=[MemberConfig("A")],
            member_config_path=None,
            max_turns=1,
            enable_tools=True,
            group_max_rounds=5,
            persist_dynamic_members=False,
        )

        assert service.group.dispatch_policy.name == "on_demand"
    finally:
        if old_policy is None:
            os.environ.pop("MAS_GROUP_DISPATCH_POLICY", None)
        else:
            os.environ["MAS_GROUP_DISPATCH_POLICY"] = old_policy
        app_builders.build_agent = old_build_agent


def test_build_group_config_policy_overrides_env() -> None:
    old_build_agent = app_builders.build_agent
    old_policy = os.environ.get("MAS_GROUP_DISPATCH_POLICY")
    app_builders.build_agent = lambda *args, **kwargs: DummyAgent()  # type: ignore[assignment]
    os.environ["MAS_GROUP_DISPATCH_POLICY"] = "on_demand"
    try:
        group = app_builders.build_group(
            AppConfig.from_env()
            .with_agent_options(max_turns=1, enable_tools=True)
            .with_group_options(
                name="review",
                max_dispatch_rounds=5,
                dispatch_policy="default",
            )
            .with_members((MemberRuntimeConfig("A"),)),
        )

        assert group.dispatch_policy.name == "default"
    finally:
        if old_policy is None:
            os.environ.pop("MAS_GROUP_DISPATCH_POLICY", None)
        else:
            os.environ["MAS_GROUP_DISPATCH_POLICY"] = old_policy
        app_builders.build_agent = old_build_agent


def test_web_add_member_persists_config(tmp_path: Path) -> None:
    old_build_agent = app_builders.build_agent
    app_builders.build_agent = lambda *args, **kwargs: DummyAgent()  # type: ignore[assignment]
    try:
        path = tmp_path / "members.json"
        state = WebState(
            group_name="default",
            member_config_path=path,
            extra_member_names=(),
            max_turns=1,
            enable_tools=True,
            group_max_rounds=5,
        )

        state.add_member(
            name="Dev",
            description="implements changes",
            base_url="https://dev.example/v1",
            api_key="dev-key",
            model="dev-model",
        )

        assert load_member_configs(path) == [
            MemberConfig(
                "Dev",
                "implements changes",
                base_url="https://dev.example/v1",
                api_key="dev-key",
                model="dev-model",
            )
        ]
    finally:
        app_builders.build_agent = old_build_agent


if __name__ == "__main__":
    test_addmember_command_accepts_description()
    test_addmember_command_accepts_member_llm_options()
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        test_member_config_load_save_merge(Path(tmp))
    with tempfile.TemporaryDirectory() as tmp:
        test_member_config_loads_url_key_aliases(Path(tmp))
    test_build_group_accepts_member_configs()
    test_build_group_reads_dispatch_policy_from_env()
    test_build_group_config_policy_overrides_env()
    with tempfile.TemporaryDirectory() as tmp:
        test_web_add_member_persists_config(Path(tmp))
