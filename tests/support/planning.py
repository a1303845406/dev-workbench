"""Synthetic planning builders and fake provider, never production configuration."""
import json
from devworkbench.modules.planning.domain import SECTIONS

def requirement(unresolved=None):
    return {"document": "\n\n".join("## " + title + "\n" + ("FR-001：用户可创建工单，成功后显示编号；空标题不允许提交。" if title == "功能需求" else "工单项目约定与可验证验收。") for title in SECTIONS), "unresolved": unresolved or []}


def node(id, parent=None, kind="module"):
    return dict(id=id, parent_id=parent, type=kind, title=id, responsibility="工单职责", exclusions="不含支付", requirement_ids=["FR-001"], acceptance="有效输入成功，空输入被拒绝", dependencies=[], rationale="按业务边界拆分")


def decomposition():
    return dict(overview="单体服务，模块边界明确", nodes=[node("M-1"), node("M-2", "M-1"), node("F-1", "M-2", "feature"), node("M-other"), node("F-other", "M-other", "feature")])


def design():
    return dict(detailed_design="F-1 F-other 接口 POST /tickets，校验标题，失败不写入", prototype_html="<html><body><h1>F-1 F-other 工单</h1><button>创建工单</button></body></html>", data_design="F-1 F-other ticket(id PRIMARY KEY, title NOT NULL)")


class FakeProvider:
    mode = "real"
    api_key = "secret-fixture-not-real"
    response = None
    seen = None
    async def complete(self, system, user):
        self.seen = (system, json.loads(user))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response or json.dumps({"reply": "请确认用户角色", "citations": ["knowledge-srs-v1"], "proposal": None})


