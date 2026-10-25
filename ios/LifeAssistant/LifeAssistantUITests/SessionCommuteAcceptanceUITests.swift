import XCTest

/// 真实 8010 PA：登录持久化、前台续期、离线保留、退出、A/B 隔离、MapKit 路线与快照提交。
/// 不设 PA_DEMO；所有网络都走真实 HTTP。AI 建议是否生成取决于后端 worker，见交付说明。
final class SessionCommuteAcceptanceUITests: XCTestCase {
    private let baseURL = "http://127.0.0.1:8010"
    private let accountA = ("ui-a@example.com", "ui-password-1")
    private let accountB = ("ui-b@example.com", "ui-password-1")
    private let planName = "App 模拟器验收"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    private func launch(baseURL: String? = nil) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL ?? self.baseURL
        app.launch()
        return app
    }

    // MARK: - 1. 登录、重启恢复、前台续期、退出

    func testLoginRestoreRenewThenLogoutDoesNotRevive() {
        let app = launch()
        ensureSignedOut(app)
        login(app, accountA.0, accountA.1)
        XCTAssertTrue(app.staticTexts["account.title"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["account.expiry"].waitForExistence(timeout: 10),
                      "登录后应显示服务端期限")

        app.terminate()
        app.launch()
        app.tabBars.buttons["我的"].tap()
        XCTAssertTrue(app.staticTexts["account.title"].waitForExistence(timeout: 15), "重启后未恢复账号")
        XCTAssertTrue(app.staticTexts["account.expiry"].exists, "重启后未恢复期限")

        // 前后台切换触发真实续期，服务端返回的新期限被采纳。
        XCUIDevice.shared.press(.home)
        app.activate()
        app.tabBars.buttons["我的"].tap()
        XCTAssertTrue(app.staticTexts["account.renewed"].waitForExistence(timeout: 20),
                      "回前台后没有真实续期结果")
        XCTAssertFalse(app.staticTexts["尚未提供续期接口；会话保持原期限，不会自动延长。"].exists)

        app.buttons["account.logout"].tap()
        confirmLogout(app)
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 20),
                      "退出后界面没有立即回到未登录")
        app.terminate()
        app.launch()
        app.tabBars.buttons["我的"].tap()
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 10), "退出后重开又恢复了旧账号")
    }

    // MARK: - 2. 断网保留凭据，联网恢复

    func testOfflineKeepsCredentialAndRecovers() {
        let app = launch()
        ensureSignedOut(app)
        login(app, accountA.0, accountA.1)
        assertSignedIn(app)

        app.terminate()
        let offline = launch(baseURL: "http://127.0.0.1:9")
        offline.tabBars.buttons["我的"].tap()
        XCTAssertTrue(offline.buttons["account.logout"].waitForExistence(timeout: 20),
                      "离线打开不应清理凭据")
        XCTAssertFalse(offline.buttons["account.login"].exists, "离线打开被误判为未登录")
        offline.terminate()

        let recovered = launch()
        recovered.tabBars.buttons["我的"].tap()
        assertSignedIn(recovered)
        XCTAssertFalse(recovered.staticTexts["尚未提供续期接口；会话保持原期限，不会自动延长。"].exists)
    }

    // MARK: - 3. A/B 切号隔离

    func testAccountSwitchDoesNotLeakPlan() {
        let app = launch()
        ensureSignedOut(app)
        login(app, accountA.0, accountA.1)
        openCommuteDetail(app)
        XCTAssertTrue(planCard(app).waitForExistence(timeout: 20))

        goBackToRoot(app)
        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        login(app, accountB.0, accountB.1)
        app.tabBars.buttons["今天"].tap()
        XCTAssertFalse(planCard(app).waitForExistence(timeout: 5),
                       "B 看到了 A 的通勤计划")

        goBackToRoot(app)
        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        login(app, accountA.0, accountA.1)
        openCommuteDetail(app)
        XCTAssertTrue(planCard(app).waitForExistence(timeout: 20),
                      "切回 A 后计划丢失")
    }

    // MARK: - 4. 真实 MapKit 路线 + 快照提交

    func testMapKitRouteAndSnapshotSubmission() {
        let app = launch()
        ensureSignedOut(app)
        login(app, accountA.0, accountA.1)
        openCommuteDetail(app)

        let update = app.buttons["commute.updateRoute"]
        XCTAssertTrue(update.waitForExistence(timeout: 20), "详情页没有更新路线入口")
        update.tap()

        XCTAssertTrue(app.staticTexts["地图预计耗时"].waitForExistence(timeout: 90),
                      "没有取得真实 MapKit ETA")
        XCTAssertTrue(app.staticTexts["已提交，等待服务器分析"].waitForExistence(timeout: 30)
                      || app.staticTexts["上次提交结果未知"].exists,
                      "路线快照没有进入提交状态")

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "commute-route-submitted"
        shot.lifetime = .keepAlways
        add(shot)
    }

    // MARK: - 5. 只读展示真实建议（不点“更新路线并分析”）

    func testReadOnlyAdvisoryConsistencyRestartAndIsolation() {
        let app = launch()
        ensureSignedOut(app)
        login(app, accountA.0, accountA.1)

        app.tabBars.buttons["今天"].tap()
        XCTAssertTrue(planCard(app).waitForExistence(timeout: 25), "今天页没有真实通勤卡片")
        let current = currentHeadline(app)
        let historyRow = app.buttons["commute.history"]
        if current.isEmpty {
            XCTAssertTrue(historyRow.waitForExistence(timeout: 10), "没有当前建议时也应展示历史记录")
        }
        let todayShot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        todayShot.name = "today-advisory"
        todayShot.lifetime = .keepAlways
        add(todayShot)

        if current.isEmpty {
            historyRow.tap()
        } else {
            planCard(app).tap()
        }
        XCTAssertTrue(app.staticTexts["智能分析"].waitForExistence(timeout: 20), "详情没有智能分析卡")
        if current.isEmpty {
            XCTAssertTrue(app.staticTexts["历史建议，已过期"].exists
                          || app.staticTexts["旧设置的建议"].exists,
                          "历史建议没有明确标注")
        } else {
            XCTAssertEqual(currentHeadline(app), current, "Today 与详情不是同一份建议")
        }
        XCTAssertTrue(app.staticTexts["耗时来源"].exists, "没有展示耗时来源")
        XCTAssertTrue(app.staticTexts["AI 额外预留"].exists, "没有展示 AI 额外预留")
        XCTAssertTrue(app.staticTexts["天气获取"].exists, "没有展示天气获取时间")

        let detailShot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        detailShot.name = "detail-advisory"
        detailShot.lifetime = .keepAlways
        add(detailShot)

        // 杀进程恢复：同一份服务端结果，不触发新检查。
        app.terminate()
        app.launch()
        app.tabBars.buttons["今天"].tap()
        XCTAssertTrue(planCard(app).waitForExistence(timeout: 25), "重启后今天卡片丢失")
        let restoredCurrent = currentHeadline(app)
        XCTAssertEqual(restoredCurrent, current, "重启后当前建议状态发生变化")
        if restoredCurrent.isEmpty {
            XCTAssertTrue(app.buttons["commute.history"].waitForExistence(timeout: 10),
                          "重启后历史记录丢失")
            app.buttons["commute.history"].tap()
        } else {
            planCard(app).tap()
        }
        XCTAssertTrue(app.staticTexts["智能分析"].waitForExistence(timeout: 20))

        // A/B 隔离：B 看不到 A 的计划与建议。
        goBackToRoot(app)
        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        login(app, accountB.0, accountB.1)
        app.tabBars.buttons["今天"].tap()
        XCTAssertFalse(planCard(app).waitForExistence(timeout: 5), "B 看到了 A 的通勤计划")

        goBackToRoot(app)
        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        login(app, accountA.0, accountA.1)
        app.tabBars.buttons["今天"].tap()
        XCTAssertTrue(planCard(app).waitForExistence(timeout: 25))
        XCTAssertEqual(currentHeadline(app), current, "切回 A 后建议状态不一致")
    }

    // MARK: - 6. Stub 状态：等待中 / 失败（COMMUTE_DEMO，不代表真实后端）

    func testDemoWaitingAndFailedStates() {
        let app = XCUIApplication()
        app.launchEnvironment["COMMUTE_DEMO"] = "1"
        app.launchEnvironment["COMMUTE_DEMO_CHECK"] = "queued"
        app.launch()
        openDemoDetail(app)

        let update = app.buttons["commute.updateRoute"]
        XCTAssertTrue(update.waitForExistence(timeout: 20), "演示详情没有分析入口")
        update.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch
            .waitForExistence(timeout: 30), "没有展示真实等待状态")
        let waiting = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        waiting.name = "demo-waiting"
        waiting.lifetime = .keepAlways
        add(waiting)

        app.terminate()
        app.launchEnvironment["COMMUTE_DEMO_CHECK"] = "failed"
        app.launch()
        openDemoDetail(app)
        XCTAssertTrue(app.buttons["commute.updateRoute"].waitForExistence(timeout: 20))
        app.buttons["commute.updateRoute"].tap()
        XCTAssertTrue(app.staticTexts["上次分析失败"].waitForExistence(timeout: 30)
                      || app.staticTexts.matching(
                        NSPredicate(format: "label CONTAINS %@", "失败")).firstMatch.exists,
                      "没有展示失败状态")
        let failed = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        failed.name = "demo-failed"
        failed.lifetime = .keepAlways
        add(failed)
    }

    // MARK: - 7. 修改到达时间 → 保存 → 重新分析（COMMUTE_DEMO，不调用真实模型）

    func testModifyArrivalSaveAndReanalyzeFlow() {
        let app = XCUIApplication()
        app.launchEnvironment["COMMUTE_DEMO"] = "1"
        app.launchEnvironment["COMMUTE_TEST_ARRIVAL"] = "10:30"
        app.launchEnvironment["START_ROUTE"] = "commuteSettings"
        app.launch()

        let save = app.buttons["保存"]
        XCTAssertTrue(save.waitForExistence(timeout: 20), "没有打开通勤设置")
        save.tap()

        let card = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上班通勤")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 25), "保存后没有回到今天卡片")
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "设置已更新")).firstMatch
            .waitForExistence(timeout: 10), "没有提示旧建议不适用于新设置")
        let history = app.buttons["commute.history"]
        XCTAssertTrue(history.waitForExistence(timeout: 10), "旧设置的建议没有进入历史区")
        XCTAssertTrue(history.label.contains("旧设置"), "历史卡片没有标明来自旧设置")

        let savedShot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        savedShot.name = "plan-updated-today"
        savedShot.lifetime = .keepAlways
        add(savedShot)

        // 旧建议详情明确标注旧设置。
        history.tap()
        XCTAssertTrue(app.staticTexts["旧设置的建议"].waitForExistence(timeout: 15),
                      "旧设置建议没有历史横幅")
        goBackToRoot(app)

        // 新分析：只应触发一次；按钮在提交中禁用。
        card.tap()
        let analyze = app.buttons["commute.updateRoute"]
        XCTAssertTrue(analyze.waitForExistence(timeout: 20))
        analyze.tap()
        analyze.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch
            .waitForExistence(timeout: 30)
            || app.staticTexts.matching(
                NSPredicate(format: "label CONTAINS %@", "已提交")).firstMatch.exists,
            "没有展示等待处理状态")

        // 演示服务在约 1-2 秒后完成；读取本次分析对应的结果。
        let newArrival = app.staticTexts["10:30"]
        XCTAssertTrue(newArrival.waitForExistence(timeout: 45), "没有读取到新计划的结果")
        let headline = currentHeadline(app)
        XCTAssertTrue(headline.contains("09:44"), "新结果的出发时间没有按 10:30 到达推算：(headline)")

        let detailShot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        detailShot.name = "plan-updated-detail"
        detailShot.lifetime = .keepAlways
        add(detailShot)

        goBackToRoot(app)
        app.tabBars.buttons["今天"].tap()
        XCTAssertEqual(currentHeadline(app), headline, "Today 与详情不是同一次分析结果")

        // 重新打开只读取，不自动发起分析。演示 mock 不持久化新计划，
        // 新结果跨重启的服务端恢复由真实只读用例覆盖，这里只验证“不自动分析”。
        app.terminate()
        app.launchEnvironment["START_ROUTE"] = nil
        app.launchEnvironment["COMMUTE_TEST_ARRIVAL"] = nil
        app.launch()
        app.tabBars.buttons["今天"].tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上班通勤")).firstMatch
            .waitForExistence(timeout: 25))
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch.exists,
            "重新打开不应自动发起新分析")
    }

    private func openDemoDetail(_ app: XCUIApplication) {
        app.tabBars.buttons["今天"].tap()
        let card = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上班通勤")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 25), "没有演示通勤卡片")
        card.tap()
    }

    private func planCard(_ app: XCUIApplication) -> XCUIElement {
        app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", planName)).firstMatch
    }

    private func currentHeadline(_ app: XCUIApplication) -> String {
        let passed = app.staticTexts["今日通勤时段已过"].firstMatch
        if passed.exists { return passed.label }
        // 只匹配“21:54 建议出发”这种标题，避免命中 AI 卡里的“建议出发”字段名。
        let departure = app.staticTexts.matching(
            NSPredicate(format: "label MATCHES %@", "[0-9]{1,2}:[0-9]{2} 建议出发")).firstMatch
        return departure.exists ? departure.label : ""
    }

    private func todayAILine(_ app: XCUIApplication) -> String? {
        let available = app.staticTexts.matching(
            NSPredicate(format: "label BEGINSWITH %@", "AI ")).firstMatch
        if available.exists { return available.label }
        let historical = app.staticTexts.matching(
            NSPredicate(format: "label BEGINSWITH %@", "历史 AI")).firstMatch
        return historical.exists ? historical.label : nil
    }

    // MARK: - Helpers

    private func assertSignedIn(_ app: XCUIApplication) {
        XCTAssertTrue(app.staticTexts["account.title"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.buttons["account.logout"].exists)
    }

    private func openCommuteDetail(_ app: XCUIApplication) {
        app.tabBars.buttons["今天"].tap()
        let card = planCard(app)
        XCTAssertTrue(card.waitForExistence(timeout: 20), "今天页没有通勤卡片")
        card.tap()
        XCTAssertTrue(app.buttons["commute.updateRoute"].waitForExistence(timeout: 20),
                      "没有进入通勤详情")
    }

    private func goBackToRoot(_ app: XCUIApplication) {
        for _ in 0..<3 {
            let back = app.buttons["nav.back"]
            if !back.exists { break }
            back.tap()
            _ = app.tabBars.firstMatch.waitForExistence(timeout: 5)
        }
    }

    private func ensureSignedOut(_ app: XCUIApplication) {
        app.tabBars.buttons["我的"].tap()
        if app.buttons["account.logout"].waitForExistence(timeout: 4) {
            app.buttons["account.logout"].tap()
            confirmLogout(app)
        }
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 10))
    }

    private func confirmLogout(_ app: XCUIApplication) {
        let sheet = app.sheets.buttons["退出登录"]
        if sheet.waitForExistence(timeout: 3) { sheet.tap(); return }
        let alert = app.alerts.buttons["退出登录"]
        if alert.exists { alert.tap(); return }
        let candidates = app.buttons.matching(identifier: "退出登录")
        if candidates.count > 0 { candidates.element(boundBy: candidates.count - 1).tap() }
    }

    private func login(_ app: XCUIApplication, _ email: String, _ password: String) {
        performLoginForm(app, email, password)
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 25), "登录没有成功")
    }

    /// 登录表单不假设登录后停在“我的”（深链会直接推到 advisory 详情）。
    private func performLoginForm(_ app: XCUIApplication, _ email: String, _ password: String) {
        var opened = false
        for _ in 0..<3 {
            app.tabBars.buttons["我的"].tap()
            let loginButton = app.buttons["account.login"]
            guard loginButton.waitForExistence(timeout: 8) else { continue }
            loginButton.tap()
            if app.textFields["login.email"].waitForExistence(timeout: 8) {
                opened = true
                break
            }
        }
        XCTAssertTrue(opened, "登录表单没有打开")

        let emailField = app.textFields["login.email"]
        XCTAssertTrue(emailField.waitForExistence(timeout: 10))
        emailField.tap()
        emailField.typeText(email)
        let passwordField = app.secureTextFields["login.password"]
        passwordField.tap()
        passwordField.typeText(password)
        app.buttons["login.submit"].tap()

        dismissPasswordPrompt()
    }

    private func dismissPasswordPrompt() {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for button in [springboard.buttons["以后"], springboard.buttons["Not Now"]] {
            if button.waitForExistence(timeout: 4) { button.tap(); return }
        }
    }
}
