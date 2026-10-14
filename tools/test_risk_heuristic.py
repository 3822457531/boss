"""Regression: resume text must not trip captcha/risk heuristic."""

from admin.boss_runner import _looks_like_risk


def test_resume_bio_not_risk():
    # 本次误报现场：PHP 岗推荐列表里常见词
    sample = """
当前岗位：PHP _ 怀化 5-6K
推荐列表（按来源分组）：共 30 人。
  - 5. 袁一皓｜可打招呼
    优势: 深耕3C数码租赁行业技术落地与风险管控；租赁行业专属风控模型
  - 7. 某候选人
    优势: 熟悉异常处理、排查线上异常，具备独立排查问题能力
"""
    assert _looks_like_risk(sample) is None


def test_real_risk_phrases_still_hit():
    assert _looks_like_risk("请完成滑动验证后继续") == "滑动验证"
    assert _looks_like_risk("检测到验证码，请人工处理") == "验证码"
    assert _looks_like_risk("登录失效，请重新登录") in ("请重新登录", "登录失效")
    assert _looks_like_risk("操作频繁，稍后再试") == "操作频繁"
    assert _looks_like_risk("账号异常，请稍后再试") == "账号异常"
