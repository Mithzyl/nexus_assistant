import XCTest

/// 联合验收用：真实 round2 隔离后端（8410）+ 控制面（8412）。
/// 应用不使用 PA_DEMO，通过 PA_API_BASE_URL 连接；只操作合成账号与临时数据库。
/// 前置：round2/tests/e2e/live_backend_fixture.py（f6448a67）已启动。
final class LiveRuntimeUITests: XCTestCase {
    private let baseURL = "http://127.0.0.1:8410"
    private let controlURL = "http://127.0.0.1:8412"
    private let password = "acceptance-password-2026"
    private let runtimeEmail = "runtime-a@example.com"
    private let runtimePlan = "运行验证通勤"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    // MARK: - 启动与登录

    private func launch() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL
        app.launch()
        return app
    }

    private func tapTab(_ app: XCUIApplication, _ name: String) {
        let tab = app.tabBars.buttons[name]
        XCTAssertTrue(tab.waitForExistence(timeout: 10), "没有找到标签页 \(name)")
        tab.tap()
        if !tab.isSelected {
            Thread.sleep(forTimeInterval: 1)
            tab.tap()
        }
        XCTAssertTrue(tab.isSelected, "没有切换到 \(name) 页")
    }

    private func signOutIfNeeded(_ app: XCUIApplication) {
        // 详情页隐藏底部标签栏：先回到根页再找账号入口。
        if app.buttons["nav.back"].exists {
            app.buttons["nav.back"].tap()
        }
        tapTab(app, "我的")
        if app.buttons["account.logout"].waitForExistence(timeout: 5) {
            app.buttons["account.logout"].tap()
            let confirm = app.sheets.buttons["退出登录"].firstMatch
            if confirm.waitForExistence(timeout: 3) { confirm.tap() }
            else { app.buttons["退出登录"].firstMatch.tap() }
        } else if app.buttons["重新登录"].waitForExistence(timeout: 3) {
            // 上次运行留下“登录状态尚未验证”的离线会话：先清掉再登录。
            app.buttons["重新登录"].tap()
        }
        _ = app.buttons["account.login"].waitForExistence(timeout: 5)
    }

    private func dismissPasswordPrompt() {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        if springboard.buttons["以后"].waitForExistence(timeout: 3) {
            springboard.buttons["以后"].tap()
        }
    }

    private func signIn(_ app: XCUIApplication, email: String) {
        tapTab(app, "我的")
        if !app.buttons["account.login"].exists,
           app.buttons["重新登录"].waitForExistence(timeout: 3) {
            app.buttons["重新登录"].tap()
        }
        if app.buttons["account.login"].waitForExistence(timeout: 8) {
            // 登录 sheet 偶尔需要第二次点击才能稳定出现；按键存在性重试，不靠坐标。
            for _ in 0..<3 {
                app.buttons["account.login"].tap()
                if app.textFields["login.email"].waitForExistence(timeout: 5) { break }
                dismissPasswordPrompt()
            }
        }
        let emailField = app.textFields["login.email"]
        if !emailField.waitForExistence(timeout: 10) {
            screenshot("live-login-missing-field")
        }
        XCTAssertTrue(emailField.waitForExistence(timeout: 5), "没有登录邮箱输入框")
        emailField.tap()
        emailField.typeText(email)
        let passwordField = app.secureTextFields["login.password"]
        passwordField.tap()
        passwordField.typeText(password)
        app.buttons["login.submit"].tap()
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 30), "登录没有成功")
        dismissPasswordPrompt()
    }

    // MARK: - 后端控制面

    private func control(_ path: String, method: String = "POST") -> [String: Any] {
        var request = URLRequest(url: URL(string: controlURL + path)!)
        request.httpMethod = method
        let semaphore = DispatchSemaphore(value: 0)
        var result: [String: Any] = [:]
        URLSession.shared.dataTask(with: request) { data, _, _ in
            if let data,
               let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                result = object
            }
            semaphore.signal()
        }.resume()
        _ = semaphore.wait(timeout: .now() + 30)
        return result
    }

    private func checks() -> [[String: Any]] {
        (control("/stats", method: "GET")["checks"] as? [[String: Any]]) ?? []
    }

    private func advisories() -> [[String: Any]] {
        (control("/stats", method: "GET")["advisories"] as? [[String: Any]]) ?? []
    }

    // MARK: - 页面操作

    private func openRuntimePlan(_ app: XCUIApplication) {
        tapTab(app, "今天")
        let card = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", runtimePlan)).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 25), "今天页没有本轮服务器计划")
        card.tap()
    }

    private func startAnalysis(_ app: XCUIApplication) {
        let update = app.buttons["commute.updateRoute"]
        XCTAssertTrue(update.waitForExistence(timeout: 25), "详情没有分析入口")
        update.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待处理")).firstMatch
            .waitForExistence(timeout: 45), "没有先显示任务已保存/等待")
    }

    private func screenshot(_ name: String) {
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = name
        shot.lifetime = .keepAlways
        add(shot)
    }

    // MARK: - 完整联网链路

    func testRuntimeEndToEndAgainstRound2Backend() {
        let app = launch()
        signOutIfNeeded(app)
        signIn(app, email: runtimeEmail)

        // 初始状态：worker 从未报到。
        _ = control("/heartbeat?state=never")
        let baseline = checks()
        let baselineIDs = Set(baseline.compactMap { $0["check_id"] as? String })
        openRuntimePlan(app)
        startAnalysis(app)

        XCTAssertTrue(app.staticTexts["尚未收到后台执行程序的响应。"]
            .waitForExistence(timeout: 30), "没有读取到真实 not_seen")
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "正在分析建议")).firstMatch.exists,
            "未领取时不能显示正在分析")
        screenshot("live-runtime-waiting")

        // 心跳报到但 worker 没有领取当前任务：只更新辅助说明。
        _ = control("/heartbeat?state=online")
        let refresh = app.buttons["刷新处理状态"].firstMatch
        XCTAssertTrue(refresh.waitForExistence(timeout: 10))
        refresh.tap()
        XCTAssertTrue(app.staticTexts["后台执行程序最近有响应。"]
            .waitForExistence(timeout: 20), "没有读取到真实 online")
        // 轮询超时后文案可能从“等待服务器处理”变为“正在处理”，但都不等于开始分析。
        let stillWaiting = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@ OR label CONTAINS %@",
                       "等待处理", "正在处理")).firstMatch
        if !stillWaiting.exists {
            screenshot("live-online-unexpected")
            print("LIVE_DEBUG_START\n\(app.debugDescription)\nLIVE_DEBUG_END")
        }
        XCTAssertTrue(stillWaiting.exists, "心跳在线不能把任务改成正在分析")
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "正在分析建议")).firstMatch.exists)
        screenshot("live-runtime-online")

        // 任务未完成时杀掉 App，确认恢复同一检查而不是新建。
        app.terminate()
        let relaunched = launch()
        openRuntimePlan(relaunched)
        let restored = relaunched.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待处理")).firstMatch
        if !restored.waitForExistence(timeout: 30) {
            screenshot("live-restart-missing")
            print("LIVE_DEBUG_START\n\(relaunched.debugDescription)\nLIVE_DEBUG_END")
        }
        XCTAssertTrue(restored.exists, "重开后没有恢复同一任务")
        XCTAssertEqual(checks().count, baseline.count + 1, "重开不应创建新检查")

        // worker 领取并完成；重启后的 App 应按 check_id 读取本次建议。
        let worker = control("/worker")
        XCTAssertEqual(worker["returncode"] as? Int, 0, "worker 执行失败：\(worker)")

        let after = checks()
        let created = after.filter { !baselineIDs.contains($0["check_id"] as? String ?? "") }
        XCTAssertEqual(created.count, 1, "应只有一条新检查")
        XCTAssertEqual(created.first?["status"] as? String, "succeeded")
        let checkID = created.first?["check_id"] as? String
        let linked = advisories().filter { ($0["check_id"] as? String) == checkID }
        XCTAssertEqual(linked.count, 1, "本次检查应关联一条建议")

        XCTAssertTrue(relaunched.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "建议出发")).firstMatch
            .waitForExistence(timeout: 30), "应用没有读取并展示本次建议")
        XCTAssertTrue(relaunched.staticTexts["最新天气"].exists)
        screenshot("live-runtime-result")

        // 乙账号看不到甲的任何计划/任务/建议。
        signOutIfNeeded(relaunched)
        signIn(relaunched, email: "acceptance-b@example.com")
        tapTab(relaunched, "今天")
        XCTAssertTrue(relaunched.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "乙的通勤计划")).firstMatch
            .waitForExistence(timeout: 25), "乙账号没有显示自己的计划")
        XCTAssertEqual(relaunched.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", runtimePlan)).count, 0,
            "乙账号仍显示甲的计划")
        screenshot("live-runtime-account-b")
    }
}
