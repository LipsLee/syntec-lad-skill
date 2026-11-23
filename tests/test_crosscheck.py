from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from syntec_lad.compiler import compile_plan
from syntec_lad.crosscheck import (
    crosscheck,
    parse_alarm_xml,
    parse_io_table,
    parse_param_xml,
    parse_pxt,
)
from syntec_lad.delivery import discover, resolve_inputs

PXT = """[3401]
title =R81
value = 0
min = -999999999
max = 999999999
[5001]
title =R8001
value = 0
min = 0
max = 1
"""

PARAM_CHS = """<?xml version="1.0" encoding="utf-8" ?>
<ResMap>
\t<Message ID="Param3401" Content="Z轴换刀点(R81)" />
\t<!--<Message ID="Param5001" Content="废弃参数(R8001)" />-->
</ResMap>
"""

PARAM_CHT = """<?xml version="1.0" encoding="utf-8" ?>
<ResMap>
\t<Message ID="Param3401" Content="Z軸換刀點(R81)" />
\t<Message ID="Param5001" Content="換刀XY(R8001)" />
</ResMap>
"""

ALARM = """<?xml version="1.0" encoding="utf-8" ?>
<ResMap>
\t<Message ID="AlarmMsg::MLC::ID=65" Content="R44.00:主轴转速异常" />
\t<Message ID="AlarmMsg::MLCHint::ID=1" Content="(R590.1)轴向未原点归复" />
</ResMap>
"""

IO_TABLE = "I000\t启动按钮\nI013\t安全门\nO120\t三色灯红\n"


def _plan(actions: list[dict]) -> dict:
    return {
        "schema": "syntec-lad-plan/v1",
        "encoding": "gb2312",
        "programs": [
            {
                "name": "MAIN",
                "networks": [
                    {"condition": {"contact": "I000"}, "actions": actions}
                ],
            }
        ],
    }


class ParserTests(unittest.TestCase):
    def test_parse_pxt_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "paramext.pxt"
            path.write_text(PXT, encoding="utf-8")
            result = parse_pxt(path)
            self.assertEqual(result["sections"], [3401, 5001])

    def test_parse_param_xml_separates_commented(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ParamExt_CHS.xml"
            path.write_text(PARAM_CHS, encoding="utf-8")
            result = parse_param_xml(path)
            self.assertIn("3401", result["live"])
            self.assertNotIn("5001", result["live"])
            self.assertEqual(result["commented"], [5001])
            self.assertEqual(result["live"]["3401"]["register"], 81)

    def test_parse_alarm_xml_bits(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AlarmPLC_CHS.xml"
            path.write_text(ALARM, encoding="utf-8")
            result = parse_alarm_xml(path)
            self.assertIn((44, 0), result["bits"])
            self.assertEqual(result["bits"][(590, 1)]["kind"], "MLCHint")

    def test_parse_io_table_point_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "io.dat"
            path.write_text(IO_TABLE, encoding="utf-8")
            result = parse_io_table(path)
            self.assertEqual(result["count"], 3)
            self.assertIn("I013", result["points"])


class CrosscheckTests(unittest.TestCase):
    def test_param_text_mismatch_and_language_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lad = root / "cnc.lad"
            compile_plan(_plan([{"op": "OUT", "operand": "O000"}])).save(lad)
            pxt = root / "paramext.pxt"
            pxt.write_text(PXT, encoding="utf-8")
            chs = root / "ParamExt_CHS.xml"
            chs.write_text(PARAM_CHS, encoding="utf-8")
            cht = root / "ParamExt_CHT.xml"
            cht.write_text(PARAM_CHT, encoding="utf-8")

            result = crosscheck(
                lad,
                pxt_path=pxt,
                param_xml_paths=[chs, cht],
            )

        self.assertEqual(result["params"]["pxt_sections"], 2)
        self.assertEqual(result["params"]["xml_live"], 1)
        self.assertEqual(result["params"]["missing_text"], [5001])
        self.assertTrue(
            any(item["param"] == 5001 for item in result["errors"]),
            "Pr5001 在 pxt 有节但无生效文案，应报错",
        )
        self.assertIn("ParamExt_CHT.xml", result["languages"]["others"])
        self.assertEqual(
            result["languages"]["others"]["ParamExt_CHT.xml"]["diff"], [5001]
        )

    def test_alarm_bits_and_io_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lad = root / "cnc.lad"
            compile_plan(_plan([{"op": "OUT", "operand": "O000"}])).save(lad)
            alarm = root / "AlarmPLC_CHS.xml"
            alarm.write_text(ALARM, encoding="utf-8")
            io = root / "io.dat"
            io.write_text(IO_TABLE, encoding="utf-8")

            result = crosscheck(lad, alarm_xml_path=alarm, io_path=io)

        self.assertIn("alarms", result)
        # 梯图没有驱动 R44/R590，文案应全部记为“未使用”
        self.assertEqual(result["alarms"]["written"], 0)
        self.assertEqual(result["alarms"]["documented"], 2)
        self.assertEqual(result["io"]["table_points"], 3)
        self.assertIn("I013", result["io"]["only_in_table"])


class DeliveryTests(unittest.TestCase):
    def _make_mb_tree(self, root: Path, lad_source: Path) -> None:
        (root / "Ladder").mkdir(parents=True)
        (root / "ParameterExt").mkdir()
        (root / "IOMap").mkdir()
        (root / "Ladder" / "cnc.lad").write_bytes(lad_source.read_bytes())
        (root / "Ladder" / "AlarmPLC_CHS.xml").write_text(ALARM, encoding="utf-8")
        (root / "ParameterExt" / "paramext.pxt").write_text(PXT, encoding="utf-8")
        (root / "ParameterExt" / "ParamExt_CHS.xml").write_text(PARAM_CHS, encoding="utf-8")
        (root / "IOMap" / "IoMap_Customer.xml").write_text(
            "<IoMapping><X>I013</X></IoMapping>", encoding="utf-8"
        )

    def test_discovers_mb_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            lad_source = base / "source.lad"
            compile_plan(_plan([{"op": "OUT", "operand": "O000"}])).save(lad_source)
            root = base / "M7R2337_20260909_MBL_TEST"
            self._make_mb_tree(root, lad_source)

            result = discover(root)

        self.assertEqual(result["layout"], "mb")
        self.assertTrue(result["files"]["lad"].endswith("cnc.lad"))
        self.assertTrue(result["files"]["pxt"].endswith("paramext.pxt"))
        self.assertIn("CHS", result["files"]["param_xml"])
        self.assertIn("CHS", result["files"]["alarm_xml"])
        self.assertEqual(result["missing"], [])

    def test_single_lad_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lad = Path(tmp) / "cnc.lad"
            compile_plan(_plan([{"op": "OUT", "operand": "O000"}])).save(lad)
            result = discover(lad)
        self.assertEqual(result["layout"], "single-file")
        self.assertTrue(result["files"]["lad"].endswith("cnc.lad"))
        self.assertIn("pxt", result["missing"])

    def test_resolve_inputs_prefers_explicit_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lad = root / "cnc.lad"
            compile_plan(_plan([{"op": "OUT", "operand": "O000"}])).save(lad)
            explicit_pxt = root / "explicit.pxt"
            explicit_pxt.write_text(PXT, encoding="utf-8")

            inputs = resolve_inputs(lad=str(lad), pxt=str(explicit_pxt))

        self.assertEqual(Path(inputs["pxt"]).name, "explicit.pxt")


if __name__ == "__main__":
    unittest.main()
