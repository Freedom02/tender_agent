from backend.app.document_recognition.storage import (
    build_company_knowledge,
    build_eligibility_knowledge,
)
from backend.app.tender_matching.tech_units import build_tech_units, project_matched_unit


def test_project_matched_unit_exposes_only_current_product_parameters() -> None:
    units = build_tech_units(
        {
            "products": [
                {
                    "productId": "PRD-001",
                    "productName": "数字孪生平台",
                    "brand": "优锘",
                    "model": "V4",
                    "description": "支持三维可视化和数据接入",
                }
            ],
            "parameters": [
                {
                    "parameterId": "PAR-001",
                    "productId": "PRD-001",
                    "description": "支持三维可视化和数据接入",
                    "evidencePath": "company/parameter/par-001.pdf",
                },
                {
                    "parameterId": "PAR-002",
                    "productId": "PRD-001",
                    "description": "支持多租户管理",
                },
            ],
        }
    )

    projected = project_matched_unit(
        units[0],
        matched_param_ids=["PAR-001"],
        matched_fragment="支持三维可视化和数据接入",
    )

    assert projected["productName"] == "数字孪生平台"
    assert projected["brand"] == "优锘"
    assert projected["model"] == "V4"
    assert projected["matchedParams"] == ["PAR-001"]
    assert projected["matchedParameterDetails"] == [
        {
            "parameterId": "PAR-001",
            "productId": "PRD-001",
            "description": "支持三维可视化和数据接入",
            "evidencePath": "company/parameter/par-001.pdf",
            "evidenceUpdatedAt": "",
        }
    ]
    assert projected["matchedFragment"] == "支持三维可视化和数据接入"


def test_company_knowledge_projection_keeps_technical_units_and_evidence_paths() -> None:
    facts = {
        "technical_product": [
            {
                "productId": "PRD-001",
                "productName": "数字孪生平台",
                "brand": "优锘",
                "model": "V4",
                "evidencePath": "libraries/company/证明材料/技术产品/1/",
            }
        ],
        "technical_parameter": [
            {
                "parameterId": "PAR-001",
                "productId": "PRD-001",
                "description": "支持三维场景加载",
                "evidencePath": "libraries/company/证明材料/技术参数/1/",
            }
        ],
    }
    company_knowledge = {
        **build_company_knowledge(facts),
        **build_eligibility_knowledge(facts),
    }

    units = build_tech_units(company_knowledge)

    assert len(units) == 1
    assert units[0]["productId"] == "PRD-001"
    assert units[0]["productName"] == "数字孪生平台"
    assert units[0]["brand"] == "优锘"
    assert units[0]["parameters"][0]["parameterId"] == "PAR-001"
    assert units[0]["parameters"][0]["description"] == "支持三维场景加载"
    projected = project_matched_unit(units[0], ["PAR-001"])
    assert projected["matchedParameterDetails"][0]["evidencePath"] == (
        "libraries/company/证明材料/技术参数/1/"
    )
