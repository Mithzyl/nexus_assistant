import XCTest

/// 模拟器连真实隔离后端（127.0.0.1:8410，见 tests/e2e/live_backend_fixture.py）：
/// 读取服务器上的通勤计划与建议、重启后恢复会话、切换账号后只看到自己的数据。
/// 这里不使用 PA_DEMO / COMMUTE_DEMO，页面上的每个字都来自服务器响应。
final class LiveBackendUITests: XCTestCase {
    private let baseURL = "http://127.0.0.1:8410"
    private let password = "acceptance-password-2026"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    private func launch() -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL
        app.launch()
        return app
    }

    private func signOutIfNeeded(_ app: XCUIApplication) {
        app.tabBars.buttons["我的"].tap()
        guard app.buttons["account.logout"].waitForExistence(timeout: 5) else { return }
        app.buttons["account.logout"].tap()
        let confirm = app.sheets.buttons["退出登录"].firstMatch
        if confirm.waitForExistence(timeout: 3) {
            confirm.tap()
        } else {
            app.buttons["退出登录"].firstMatch.tap()
        }
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 20), "退出登录后应显示登录入口")
    }

    private func signIn(_ app: XCUIApplication, email: String) {
        app.tabBars.buttons["我的"].tap()
        if app.buttons["account.login"].waitForExistence(timeout: 5) {
            app.buttons["account.login"].tap()
        }
        let emailField = app.textFields["login.email"]
        XCTAssertTrue(emailField.waitForExistence(timeout: 10), "没有登录邮箱输入框")
        emailField.tap()
        emailField.typeText(email)
        let passwordField = app.secureTextFields["login.password"]
        passwordField.tap()
        passwordField.typeText(password)
        app.buttons["login.submit"].tap()
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 25), "登录没有进入已登录状态")
    }

    private func waitForTodayText(_ app: XCUIApplication, _ text: String, timeout: TimeInterval = 30) {
        app.tabBars.buttons["今天"].tap()
        let predicate = NSPredicate(format: "label CONTAINS %@", text)
        XCTAssertTrue(
            app.staticTexts.matching(predicate).firstMatch.waitForExistence(timeout: timeout),
            "今天页没有出现服务器数据：\(text)"
        )
    }

    func testReadRestoreAndAccountSwitchAgainstIsolatedBackend() {
        var app = launch()
        signOutIfNeeded(app)

        // 1. 账号 A 从服务器读取计划与已校验的模型建议
        signIn(app, email: "acceptance-a@example.com")
        waitForTodayText(app, "甲的通勤计划")
        let aiPredicate = NSPredicate(format: "label CONTAINS %@", "AI 可用")
        XCTAssertTrue(
            app.staticTexts.matching(aiPredicate).firstMatch.waitForExistence(timeout: 20),
            "今天页没有展示服务端校验通过的 AI 结果"
        )
        add(XCTAttachment(screenshot: app.screenshot()))

        // 2. 终止并重启：会话从 Keychain 恢复，数据仍来自服务器
        app.terminate()
        app = launch()
        app.tabBars.buttons["我的"].tap()
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 25), "重启后登录状态丢失")
        waitForTodayText(app, "甲的通勤计划")
        XCTAssertTrue(
            app.staticTexts.matching(aiPredicate).firstMatch.waitForExistence(timeout: 20),
            "重启后没有恢复 AI 结果"
        )
        add(XCTAttachment(screenshot: app.screenshot()))

        // 3. 切换到账号 B：只看到 B 的计划，A 的数据不残留
        signOutIfNeeded(app)
        signIn(app, email: "acceptance-b@example.com")
        waitForTodayText(app, "乙的通勤计划")
        let stalePredicate = NSPredicate(format: "label CONTAINS %@", "甲的通勤计划")
        XCTAssertEqual(
            app.staticTexts.matching(stalePredicate).count, 0,
            "切换账号后仍显示上一个账号的通勤计划"
        )
        add(XCTAttachment(screenshot: app.screenshot()))
    }
}
