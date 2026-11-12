"""Unit tests for the pure startup-screen classifier (candidate)."""

from __future__ import annotations

import copy
import math
import unittest

from autostory import startup_screens as ss

GAME = ss.GAME_PACKAGE
AOSP = "com.android.packageinstaller"
GOOGLE = "com.google.android.packageinstaller"


def ocr(text, score=0.95, x=100.0, y=100.0, box=None):
    if box is None:
        box = [x - 10.0, y - 10.0, x + 10.0, y + 10.0]
    return {"text": text, "score": score, "x": x, "y": y, "box": box}


def classify(texts, foreground=GAME, page=None):
    return ss.classify(texts, foreground, page)


class ResultShapeTests(unittest.TestCase):
    def assert_shape(self, result):
        self.assertEqual(set(result), {"state", "reason", "progress", "button"})
        self.assertIsInstance(result["state"], str)
        self.assertIsInstance(result["reason"], str)
        self.assertTrue(result["reason"].strip())
        self.assertRegex(result["reason"], r"[\u4e00-\u9fff]")
        self.assertTrue(
            result["progress"] is None or isinstance(result["progress"], float)
        )
        self.assertTrue(result["button"] is None or isinstance(result["button"], dict))
        if result["button"] is not None:
            self.assertEqual(
                set(result["button"]), {"x", "y", "text", "score"}
            )
            self.assertIsInstance(result["button"]["x"], float)
            self.assertIsInstance(result["button"]["y"], float)
            self.assertIsInstance(result["button"]["text"], str)
            self.assertIsInstance(result["button"]["score"], float)

    def test_all_states_have_the_documented_shape(self):
        cases = [
            classify([ocr("采购中心")], page="home"),
            classify([ocr("放弃行动"), ocr("继续行动")]),
            classify([ocr("明日方舟"), ocr("更新"), ocr("取消")], AOSP),
            classify([ocr("明日方舟"), ocr("正在安装.."), ocr("取消")], AOSP),
            classify(
                [ocr("明日方舟"), ocr("安装完成"), ocr("打开")], AOSP
            ),
            classify([ocr("发现新版本"), ocr("更新")]),
            classify([ocr("正在下载资源"), ocr("45 %")]),
            classify([ocr("正在释放神经递质")]),
            classify([ocr("START")]),
            classify([ocr("登录")]),
            classify([ocr("一些噪声文字")]),
        ]
        for result in cases:
            with self.subTest(result=result):
                self.assert_shape(result)


class ForegroundGateTests(unittest.TestCase):
    def test_missing_foreground_is_unknown(self):
        result = classify([ocr("START")], foreground=None)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_empty_foreground_is_unknown(self):
        self.assertEqual(classify([ocr("START")], foreground="   ")["state"], "unknown")

    def test_launcher_is_unknown_even_with_game_text(self):
        result = classify(
            [ocr("明日方舟"), ocr("START")], foreground="com.miui.home"
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_package_suffix_is_not_the_official_package(self):
        result = classify(
            [ocr("START")], foreground="com.hypergryph.arknights.bilibili"
        )
        self.assertEqual(result["state"], "unknown")

    def test_unknown_installer_is_unknown(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新"), ocr("取消")],
            foreground="com.example.installer",
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_exact_package_with_surrounding_whitespace_is_accepted(self):
        result = classify([ocr("START")], foreground=f"  {GAME}  ")
        self.assertEqual(result["state"], "login_start")


class InstallerTests(unittest.TestCase):
    def test_saved_install_confirm_with_update_and_cancel(self):
        texts = [
            ocr("明日方舟", 0.999, 494.5, 257.5, [405, 234, 584, 281]),
            ocr("更新", 0.999, 921.0, 477.0, [886, 457, 956, 497]),
            ocr("取消", 0.999, 780.0, 477.0, [745, 457, 815, 497]),
        ]
        result = classify(texts, AOSP)
        self.assertEqual(result["state"], "install_confirm")
        self.assertEqual(result["button"]["text"], "更新")
        self.assertEqual((result["button"]["x"], result["button"]["y"]), (921.0, 477.0))
        self.assertIsNone(result["progress"])

    def test_google_installer_accepts_install_button(self):
        result = classify(
            [ocr("明日方舟"), ocr("安装", 0.95), ocr("取消")], GOOGLE
        )
        self.assertEqual(result["state"], "install_confirm")
        self.assertEqual(result["button"]["text"], "安装")

    def test_wrong_app_label_is_unknown(self):
        result = classify(
            [ocr("崩坏：星穹铁道"), ocr("更新"), ocr("取消")], AOSP
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_missing_app_label_is_unknown(self):
        result = classify([ocr("更新"), ocr("取消")], AOSP)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_game_foreground_cannot_infer_installer_identity(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新"), ocr("取消")], foreground=GAME
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_two_matching_install_buttons_are_ambiguous(self):
        texts = [
            ocr("明日方舟"),
            ocr("更新", x=900, y=470),
            ocr("安装", x=1050, y=470),
        ]
        result = classify(texts, AOSP)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_duplicate_ocr_of_same_control_is_not_ambiguous(self):
        texts = [
            ocr("明日方舟"),
            ocr("更新", x=921, y=477),
            ocr("更新", x=921.4, y=477.2),
        ]
        result = classify(texts, AOSP)
        self.assertEqual(result["state"], "install_confirm")
        self.assertEqual(result["button"]["text"], "更新")

    def test_suffix_button_is_not_exact(self):
        result = classify(
            [ocr("明日方舟"), ocr("立即更新"), ocr("取消")], AOSP
        )
        self.assertEqual(result["state"], "unknown")

    def test_low_confidence_app_label_is_not_enough(self):
        result = classify(
            [ocr("明日方舟", 0.79), ocr("更新"), ocr("取消")], AOSP
        )
        self.assertEqual(result["state"], "unknown")

    def test_low_confidence_action_is_not_clickable(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新", 0.89), ocr("取消")], AOSP
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_saved_installing_ocr(self):
        # The exact OCR shape saved in runtime/scan0930-update.json (after).
        texts = [
            ocr("12+", 0.996, 1239.0, 43.0, [1220, 29, 1258, 57]),
            ocr("CADPA", 0.993, 1238.0, 65.5, [1219, 59, 1257, 72]),
            ocr("适龄提示", 0.999, 1238.0, 80.5, [1213, 72, 1263, 89]),
            ocr("明日方舟", 0.999, 494.5, 257.5, [405, 234, 584, 281]),
            ocr("正在安装..", 0.966, 398.5, 336.5, [312, 314, 485, 359]),
            ocr("取消", 0.999, 920.0, 477.5, [885, 457, 955, 498]),
            ocr("处于未开发状态。", 0.997, 1079.0, 540.5, [1011, 529, 1147, 552]),
            ocr("乌萨斯帝国", 0.997, 143.0, 567.5, [55, 548, 231, 587]),
            ocr("0 %", 0.843, 21.0, 706.5, [0, 694, 42, 719]),
        ]
        result = classify(texts, AOSP)
        self.assertEqual(result["state"], "installing")
        self.assertIsNone(result["button"])
        self.assertIsNone(result["progress"])

    def test_installing_requires_exact_app_label(self):
        result = classify([ocr("正在安装.."), ocr("取消")], AOSP)
        self.assertEqual(result["state"], "unknown")

    def test_install_done_with_open_button(self):
        result = classify(
            [ocr("明日方舟"), ocr("安装完成"), ocr("打开", 0.97, 900, 500)], AOSP
        )
        self.assertEqual(result["state"], "install_done")
        self.assertEqual(result["button"]["text"], "打开")

    def test_install_done_without_open_button_is_unknown(self):
        result = classify([ocr("明日方舟"), ocr("已安装"), ocr("取消")], AOSP)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_already_installed_status_with_update_button_is_confirm(self):
        result = classify(
            [ocr("明日方舟"), ocr("已安装"), ocr("更新"), ocr("取消")], AOSP
        )
        self.assertEqual(result["state"], "install_confirm")
        self.assertEqual(result["button"]["text"], "更新")

    def test_install_done_with_two_open_buttons_is_unknown(self):
        result = classify(
            [
                ocr("明日方舟"),
                ocr("安装完成"),
                ocr("打开", x=800, y=500),
                ocr("打开", x=1000, y=500),
            ],
            AOSP,
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_installer_untrusted_source_is_attention(self):
        result = classify(
            [ocr("明日方舟"), ocr("未知来源"), ocr("安装")], AOSP
        )
        self.assertEqual(result["state"], "attention")
        self.assertIsNone(result["button"])


class UpdateConfirmTests(unittest.TestCase):
    def test_mandatory_update_with_confirm_button(self):
        result = classify(
            [ocr("发现新版本，请更新后继续游戏"), ocr("确认", 0.96, 900, 500)]
        )
        self.assertEqual(result["state"], "update_confirm")
        self.assertEqual(result["button"]["text"], "确认")

    def test_resource_update_with_download_button(self):
        result = classify([ocr("资源更新"), ocr("下载", 0.95, 900, 500)])
        self.assertEqual(result["state"], "update_confirm")
        self.assertEqual(result["button"]["text"], "下载")

    def test_update_with_purchase_is_attention(self):
        result = classify(
            [ocr("发现新版本，请更新后继续游戏"), ocr("购买源石"), ocr("确认")]
        )
        self.assertEqual(result["state"], "attention")
        self.assertIsNone(result["button"])

    def test_purchase_screen_with_confirm_is_attention(self):
        result = classify([ocr("是否花费源石购买合成玉"), ocr("确认")])
        self.assertEqual(result["state"], "attention")
        self.assertIsNone(result["button"])

    def test_update_with_deletion_is_attention(self):
        result = classify([ocr("发现新版本"), ocr("删除存档"), ocr("确认")])
        self.assertEqual(result["state"], "attention")

    def test_arbitrary_text_containing_update_is_not_update_confirm(self):
        for text in (
            "更新公告：新活动开启",
            "更新内容：新增关卡",
            "本次更新包含以下内容",
            "活动更新说明",
        ):
            with self.subTest(text=text):
                result = classify([ocr(text), ocr("确认")])
                self.assertNotEqual(result["state"], "update_confirm")

    def test_two_update_actions_are_ambiguous(self):
        result = classify(
            [
                ocr("发现新版本"),
                ocr("确认", x=800, y=500),
                ocr("更新", x=1000, y=500),
            ]
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_low_confidence_update_action_is_not_clickable(self):
        result = classify([ocr("发现新版本"), ocr("更新", 0.89, 900, 500)])
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_update_without_action_is_unknown(self):
        self.assertEqual(classify([ocr("发现新版本")])["state"], "unknown")

    def test_latest_version_negative_is_not_a_prompt(self):
        result = classify([ocr("已是最新版本"), ocr("更新")], page="home")
        self.assertEqual(result["state"], "ready")

    def test_installer_wording_in_game_foreground_is_not_update_confirm(self):
        result = classify([ocr("要更新此应用吗？"), ocr("更新")])
        self.assertEqual(result["state"], "unknown")

    def test_game_update_wording_with_this_app_is_allowed(self):
        result = classify(
            [ocr("此应用需要更新后才能继续游戏"), ocr("更新")]
        )
        self.assertEqual(result["state"], "update_confirm")

    def test_game_update_mentioning_install_package_is_allowed(self):
        result = classify([ocr("版本更新，请下载安装包"), ocr("下载")])
        self.assertEqual(result["state"], "update_confirm")

    def test_resource_marker_with_progress_and_download_button_is_prompt(self):
        result = classify(
            [ocr("资源下载"), ocr("45 %"), ocr("下载", 0.95, 900, 500)]
        )
        self.assertEqual(result["state"], "update_confirm")


class BattleTests(unittest.TestCase):
    def test_paused_battle_overrides_verified_page(self):
        result = classify(
            [ocr("放弃行动"), ocr("继续行动")], page="home"
        )
        self.assertEqual(result["state"], "battle")
        self.assertIsNone(result["button"])

    def test_settlement_overrides_verified_page(self):
        result = classify(
            [ocr("行动结束"), ocr("MISSION RESULTS")], page="map"
        )
        self.assertEqual(result["state"], "settlement")
        self.assertIsNone(result["button"])

    def test_active_battle_hud(self):
        result = classify(
            [ocr("技能"), ocr("撤退"), ocr("二倍速")], page="home"
        )
        self.assertEqual(result["state"], "battle")

    def test_skill_plus_speed_is_battle_even_without_retreat(self):
        result = classify([ocr("技能"), ocr("二倍速")], page="home")
        self.assertEqual(result["state"], "battle")

    def test_skill_and_retreat_without_battle_marker_is_not_battle(self):
        # Quick formation can show both labels; it must not be mistaken for a
        # battle without a battle-specific marker.
        result = classify([ocr("技能"), ocr("撤退")], page="home")
        self.assertEqual(result["state"], "ready")

    def test_pause_with_skill_is_battle(self):
        result = classify([ocr("暂停"), ocr("技能")], page="home")
        self.assertEqual(result["state"], "battle")

    def test_retreat_with_speed_is_battle(self):
        result = classify([ocr("撤退"), ocr("二倍速")], page="home")
        self.assertEqual(result["state"], "battle")


class AttentionTests(unittest.TestCase):
    def test_login_screen_overrides_verified_page(self):
        result = classify([ocr("登录"), ocr("开始唤醒")], page="home")
        self.assertEqual(result["state"], "attention")
        self.assertIsNone(result["button"])

    def test_password_screen_overrides_verified_page(self):
        result = classify([ocr("请输入密码"), ocr("确认")], page="terminal")
        self.assertEqual(result["state"], "attention")

    def test_captcha_screen_overrides_verified_page(self):
        result = classify([ocr("验证码"), ocr("点击验证")], page="map")
        self.assertEqual(result["state"], "attention")

    def test_account_switch_overrides_verified_page(self):
        result = classify([ocr("切换账号")], page="home")
        self.assertEqual(result["state"], "attention")

    def test_network_error_overrides_verified_page(self):
        result = classify([ocr("网络错误"), ocr("重试")], page="home")
        self.assertEqual(result["state"], "attention")

    def test_maintenance_is_attention(self):
        result = classify([ocr("服务器维护中，请稍后再试")])
        self.assertEqual(result["state"], "attention")

    def test_permission_dialog_is_attention(self):
        result = classify([ocr("存储权限"), ocr("允许访问")])
        self.assertEqual(result["state"], "attention")

    def test_untrusted_source_is_attention_even_for_installer(self):
        result = classify(
            [ocr("明日方舟"), ocr("未知来源"), ocr("安装")], AOSP
        )
        self.assertEqual(result["state"], "attention")

    def test_age_rating_banner_is_not_attention(self):
        result = classify(
            [ocr("12+"), ocr("CADPA"), ocr("适龄提示")], page="home"
        )
        self.assertEqual(result["state"], "ready")

    def test_network_detection_button_is_not_a_network_error(self):
        result = classify(
            [ocr("网络检测"), ocr("清除缓存"), ocr("正在加载")], page="home"
        )
        self.assertEqual(result["state"], "loading")


class LoadingDownloadTests(unittest.TestCase):
    def test_server_connection_at_one_hundred_percent_is_still_loading(self):
        rows=[ocr('正在尝试与Rhodes Island取得神经连接'),ocr('100%')]
        self.assertEqual(classify(rows)['state'],'loading')
        self.assertEqual(classify(rows)['progress'],100.)
        self.assertEqual(classify(rows+[ocr('连接失败')])['state'],'attention')

    def test_rhodes_loading_needs_position_and_independent_footer(self):
        entries=[ocr('RHODES ISLAND',x=641,y=444),ocr('进驻控制中枢的干员越多，心情消耗越少',x=640,y=670)]
        self.assertEqual(classify(entries)['state'],'loading')
        self.assertEqual(classify(entries[:1])['state'],'unknown')
        bad=[dict(row) for row in entries];bad[0]['x']=30
        self.assertEqual(classify(bad)['state'],'unknown')

    def test_copyright_health_splash_is_passive_loading(self):
        entries=[ocr('HYPERGRYPH'),ocr('STUDIO'),ocr('健康游戏忠告：抵制不良游戏，拒绝盗版游戏')]
        self.assertEqual(classify(entries)['state'],'loading')
        self.assertIsNone(classify(entries)['button'])

    def test_small_start_diamond_page_hands_off_to_maa_template(self):
        entries=[ocr('清除缓存',x=84,y=48),ocr('网络检测',x=230,y=48),ocr('Ver:2.7.91',x=85,y=78)]
        self.assertEqual(classify(entries)['state'],'login_start')
        for index in range(3):
            bad=[dict(row) for row in entries];bad[index]['x']=900
            self.assertEqual(classify(bad)['state'],'unknown')
        self.assertEqual(classify(entries+[ocr('请输入验证码')])['state'],'attention')

    def test_real_cached_account_title_is_not_an_account_dialog(self):
        entries=[ocr('ARKNIGHTS',x=639,y=421),ocr('开始唤醒',x=642,y=512),
                 ocr('扫码登录',x=607,y=683),ocr('账号管理',x=769,y=683)]
        self.assertEqual(classify(entries)['state'],'login_start')
        for text in ('请输入密码','输入验证码','账号已失效','请重新登录'):
            self.assertEqual(classify(entries+[ocr(text,x=640,y=330)])['state'],'attention')

    def test_title_exception_requires_brand_and_wakeup_position(self):
        for start in (ocr('开始唤醒',x=80,y=100),ocr('开始唤醒',score=.5,x=642,y=512)):
            self.assertEqual(classify([ocr('ARKNIGHTS'),start,ocr('账号管理',x=769,y=683)])['state'],'attention')

    def test_currency_labels_on_verified_home_do_not_imply_a_payment(self):
        self.assertEqual(classify([ocr('源石'),ocr('合成玉')],page='home')['state'],'ready')
        self.assertEqual(classify([ocr('源石'),ocr('确认')],page='home')['state'],'attention')
        self.assertEqual(classify([ocr('￥30'),ocr('月卡')],page='home')['state'],'ready')
        self.assertEqual(classify([ocr('￥30'),ocr('确认')],page='home')['state'],'attention')

    def test_healthy_gaming_slogan_is_not_a_permission_dialog(self):
        result=classify([ocr('抵制不良游戏，拒绝盗版游戏。注意自我保护，谨防受骗上当。'),
                         ocr('正在加载')])
        self.assertEqual(result['state'],'loading')

    def test_action_coordinates_must_be_inside_image(self):
        for x,y in ((1280,400),(500,720)):
            self.assertIsNone(classify([ocr('明日方舟'),ocr('更新',x=x,y=y)],AOSP)['button'])

    def test_saved_loading_neurotransmitter_progress(self):
        result = classify(
            [ocr("正在释放神经递质（64MB/1140MB）"), ocr("6 %", 0.84, 21, 706)]
        )
        self.assertEqual(result["state"], "loading")
        self.assertEqual(result["progress"], 6.0)

    def test_loading_43_percent(self):
        result = classify([ocr("正在释放神经递质（496MB/1140MB）"), ocr("43 %")])
        self.assertEqual(result["state"], "loading")
        self.assertEqual(result["progress"], 43.0)

    def test_loading_resource_check_74_percent(self):
        result = classify(
            [ocr("正在检查资源完整性，不消耗流量…"), ocr("74 %")]
        )
        self.assertEqual(result["state"], "loading")
        self.assertEqual(result["progress"], 74.0)

    def test_loading_without_percent(self):
        result = classify([ocr("正在加载")])
        self.assertEqual(result["state"], "loading")
        self.assertIsNone(result["progress"])

    def test_loading_text_mentioning_new_version_is_still_loading(self):
        result = classify([ocr("正在加载新版本资源"), ocr("12 %")])
        self.assertEqual(result["state"], "loading")
        self.assertEqual(result["progress"], 12.0)

    def test_download_with_percent(self):
        result = classify([ocr("正在下载资源"), ocr("45%")])
        self.assertEqual(result["state"], "download")
        self.assertEqual(result["progress"], 45.0)

    def test_download_with_decimal_fullwidth_percent(self):
        result = classify([ocr("下载中"), ocr("进度：45.5％")])
        self.assertEqual(result["state"], "download")
        self.assertEqual(result["progress"], 45.5)

    def test_download_active_without_percent(self):
        result = classify([ocr("正在下载")])
        self.assertEqual(result["state"], "download")
        self.assertIsNone(result["progress"])

    def test_resource_download_with_percent_without_action(self):
        result = classify([ocr("资源下载"), ocr("100 %")])
        self.assertEqual(result["state"], "download")
        self.assertEqual(result["progress"], 100.0)

    def test_zero_percent_is_valid(self):
        result = classify([ocr("正在下载资源"), ocr("0 %")])
        self.assertEqual(result["state"], "download")
        self.assertEqual(result["progress"], 0.0)

    def test_out_of_range_percent_is_not_progress(self):
        result = classify([ocr("正在下载资源"), ocr("101%")])
        self.assertEqual(result["state"], "download")
        self.assertIsNone(result["progress"])

    def test_multiple_distinct_percents_are_ambiguous(self):
        result = classify([ocr("正在下载资源"), ocr("10%"), ocr("20%")])
        self.assertEqual(result["state"], "download")
        self.assertIsNone(result["progress"])

    def test_negative_percent_is_not_progress(self):
        result = classify([ocr("正在下载资源"), ocr("-5%")])
        self.assertIsNone(result["progress"])

    def test_low_confidence_percent_is_not_progress(self):
        result = classify(
            [ocr("正在下载资源"), ocr("45%", score=0.79)]
        )
        self.assertIsNone(result["progress"])

    def test_spinner_text_is_not_progress(self):
        result = classify([ocr("正在加载"), ocr("◐")])
        self.assertEqual(result["state"], "loading")
        self.assertIsNone(result["progress"])

    def test_age_rating_12_plus_is_not_a_percent(self):
        result = classify([ocr("正在加载"), ocr("12+")])
        self.assertIsNone(result["progress"])


class LoginStartTests(unittest.TestCase):
    def test_start_token(self):
        self.assertEqual(classify([ocr("START")])["state"], "login_start")

    def test_wake_up_label(self):
        self.assertEqual(classify([ocr("开始唤醒")])["state"], "login_start")

    def test_click_to_start_label(self):
        self.assertEqual(classify([ocr("点击开始")])["state"], "login_start")

    def test_generic_start_is_not_enough(self):
        self.assertEqual(classify([ocr("开始")])["state"], "unknown")

    def test_login_text_wins_over_start(self):
        result = classify([ocr("START"), ocr("登录")])
        self.assertEqual(result["state"], "attention")


class ReadyTests(unittest.TestCase):
    def test_verified_home(self):
        result = classify(
            [ocr("采购中心"), ocr("公开招募"), ocr("干员寻访")], page="home"
        )
        self.assertEqual(result["state"], "ready")
        self.assertIsNone(result["button"])

    def test_verified_operator_page(self):
        result = classify([ocr("精英化"), ocr("潜能")], page="operator")
        self.assertEqual(result["state"], "ready")

    def test_invalid_page_is_not_trusted(self):
        result = classify([ocr("随便的文字")], page="settings")
        self.assertEqual(result["state"], "unknown")

    def test_malformed_page_is_ignored_without_crashing(self):
        for page in ([], {}, 42, b"home"):
            with self.subTest(page=page):
                result = classify([ocr("随便的文字")], page=page)
                self.assertEqual(result["state"], "unknown")

    def test_page_with_loading_marker_is_loading(self):
        result = classify([ocr("正在加载")], page="home")
        self.assertEqual(result["state"], "loading")

    def test_page_with_update_prompt_is_update_confirm(self):
        result = classify(
            [ocr("发现新版本"), ocr("确认")], page="home"
        )
        self.assertEqual(result["state"], "update_confirm")

    def test_stage_detail_two_controls(self):
        result = classify([ocr("代理指挥"), ocr("开始行动")])
        self.assertEqual(result["state"], "ready")
        self.assertIsNone(result["button"])

    def test_stage_detail_with_practice_control(self):
        result = classify([ocr("演习"), ocr("开始突袭")])
        self.assertEqual(result["state"], "ready")

    def test_stage_start_alone_is_not_enough(self):
        result = classify([ocr("开始行动")])
        self.assertEqual(result["state"], "unknown")

    def test_stage_second_control_alone_is_not_enough(self):
        result = classify([ocr("代理指挥")])
        self.assertEqual(result["state"], "unknown")

    def test_two_stage_start_buttons_are_ambiguous(self):
        result = classify(
            [
                ocr("代理指挥"),
                ocr("开始行动", x=800, y=650),
                ocr("开始作战", x=1050, y=650),
            ]
        )
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_stage_start_below_action_threshold_is_not_ready(self):
        result = classify([ocr("代理指挥"), ocr("开始行动", 0.89)])
        self.assertEqual(result["state"], "unknown")

    def test_high_noise_ocr_is_unknown(self):
        texts = [
            ocr("OPERATION"),
            ocr("一些无法归类的剧情文字"),
            ocr("NEW"),
            ocr("清除缓存"),
            ocr("网络检测"),
            ocr("Ver.2.7.91"),
            ocr("12+"),
            ocr("CADPA"),
            ocr("适龄提示"),
        ]
        result = classify(texts)
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])


class CollectionResumeTests(unittest.TestCase):
    def tabs(self):
        return [ocr('默认进度',x=738,y=42),ocr('奖励进度',x=848,y=42),
                ocr('完成进度',x=959,y=42)]

    def test_scrolled_collection_is_ready_without_cards(self):
        result=classify(self.tabs())
        self.assertEqual(result['state'],'ready')
        self.assertIsNone(result['button'])

    def test_missing_duplicate_or_misplaced_tab_is_unknown(self):
        tabs=self.tabs()
        for entries in (tabs[:2],tabs+[tabs[0]],
                        tabs[:2]+[ocr('完成进度',x=959,y=300)],
                        tabs[:2]+[ocr('完成进度',score=.8,x=959,y=42)]):
            with self.subTest(entries=entries):
                self.assertEqual(classify(entries)['state'],'unknown')

    def test_overlay_and_foreground_still_take_priority(self):
        for label in ('网络连接失败','密码','验证码'):
            self.assertEqual(classify(self.tabs()+[ocr(label)])['state'],'attention')
        self.assertEqual(classify(self.tabs(),'other.untrusted.app')['state'],'unknown')


class InvalidEvidenceTests(unittest.TestCase):
    def assert_unknown_without_button(self, result):
        self.assertEqual(result["state"], "unknown")
        self.assertIsNone(result["button"])

    def test_nan_button_x_never_allows_input(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新", x=float("nan")), ocr("取消")], AOSP
        )
        self.assert_unknown_without_button(result)

    def test_infinite_button_y_never_allows_input(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新", y=float("inf")), ocr("取消")], AOSP
        )
        self.assert_unknown_without_button(result)

    def test_nan_score_never_allows_input(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新", score=float("nan")), ocr("取消")], AOSP
        )
        self.assert_unknown_without_button(result)

    def test_score_above_one_is_invalid(self):
        result = classify(
            [ocr("明日方舟"), ocr("更新", score=1.2), ocr("取消")], AOSP
        )
        self.assert_unknown_without_button(result)

    def test_negative_and_out_of_screen_coordinates_are_invalid(self):
        for x, y in ((-1, 100), (100, -1), (1281, 100), (100, 721)):
            with self.subTest(x=x, y=y):
                result = classify(
                    [ocr("明日方舟"), ocr("更新", x=x, y=y), ocr("取消")], AOSP
                )
                self.assert_unknown_without_button(result)

    def test_malformed_box_is_invalid(self):
        result = classify(
            [
                ocr("明日方舟"),
                ocr("更新", box=[10, 10, 10, 20]),
                ocr("取消"),
            ],
            AOSP,
        )
        self.assert_unknown_without_button(result)

    def test_invalid_app_label_coordinates_are_not_identity(self):
        result = classify(
            [ocr("明日方舟", x=float("inf")), ocr("更新"), ocr("取消")], AOSP
        )
        self.assert_unknown_without_button(result)

    def test_invalid_entries_do_not_create_stage_detail(self):
        result = classify(
            [
                ocr("代理指挥"),
                ocr("开始行动", score=float("nan")),
            ]
        )
        self.assert_unknown_without_button(result)

    def test_none_and_malformed_texts_are_safe(self):
        for texts in (None, "not-a-list", 42, {"text": "START"}):
            with self.subTest(texts=texts):
                result = classify(texts)
                self.assertEqual(result["state"], "unknown")
                self.assertIsNone(result["button"])
        result = classify([None, 42, "text", {"text": None}, {}])
        self.assertEqual(result["state"], "unknown")

    def test_input_is_not_mutated(self):
        texts = [
            ocr("明日方舟"),
            ocr("更新", x=921.0, y=477.0),
            ocr("取消"),
        ]
        before = copy.deepcopy(texts)
        classify(texts, AOSP)
        self.assertEqual(texts, before)


if __name__ == "__main__":
    unittest.main(verbosity=2)
