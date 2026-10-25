import XCTest

/// 真实隔离后端（127.0.0.1:8410）上的后台状态验证：应用自己登录并读取 runtime，不使用演示仓库。
/// 后端由一个无 worker 的隔离实例提供；测试只创建 check，不会调用真实模型。
final class LiveRuntimeUITests: XCTestCase {
    private let baseURL = "http://127.0.0.1:8410"
    private let email = "runtime-a@example.com"
    private let password = "acceptance-password-2026"
    private let planName = "运行验证通勤"

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
        if confirm.waitForExistence(timeout: 3) { confirm.tap() }
        else { app.buttons["退出登录"].firstMatch.tap() }
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 20))
    }

    private func signIn(_ app: XCUIApplication) {
        app.tabBars.buttons["我的"].tap()
        if app.buttons["account.login"].waitForExistence(timeout: 5) {
            app.buttons["account.login"].tap()
        }
        let emailField = app.textFields["login.email"]
        XCTAssertTrue(emailField.waitForExistence(timeout: 10))
        emailField.tap()
        emailField.typeText(email)
        let passwordField = app.secureTextFields["login.password"]
        passwordField.tap()
        passwordField.typeText(password)
        app.buttons["login.submit"].tap()
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 25), "登录没有成功")
        dismissPasswordPrompt(app)
    }

    private func dismissPasswordPrompt(_ app: XCUIApplication) {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        if springboard.buttons["以后"].waitForExistence(timeout: 3) {
            springboard.buttons["以后"].tap()
        } else if app.buttons["以后"].waitForExistence(timeout: 1) {
            app.buttons["以后"].tap()
        }
    }

    private func openPlanAndStartTask(_ app: XCUIApplication) {
        let todayTab = app.tabBars.buttons["今天"]
        XCTAssertTrue(todayTab.waitForExistence(timeout: 10))
        todayTab.tap()
        if !todayTab.isSelected {
            Thread.sleep(forTimeInterval: 1)
            todayTab.tap()
        }
        XCTAssertTrue(todayTab.isSelected, "没有切换到今天页")
        let todayShot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        todayShot.name = "live-today"
        todayShot.lifetime = .keepAlways
        add(todayShot)
        let card = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", planName)).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 40), "今天页没有服务器计划")
        card.tap()
        let update = app.buttons["commute.updateRoute"]
        XCTAssertTrue(update.waitForExistence(timeout: 25), "详情没有分析入口")
        update.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch
            .waitForExistence(timeout: 45), "没有显示任务已保存")
    }

    func testRuntimeNotSeenAgainstIsolatedBackend() {
        let app = launch()
        signOutIfNeeded(app)
        signIn(app)
        openPlanAndStartTask(app)

        XCTAssertTrue(app.staticTexts["尚未收到后台执行程序的响应。"]
            .waitForExistence(timeout: 25), "应用没有读取到真实 not_seen 状态")
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上次分析失败")).firstMatch.exists,
            "没有 worker 不等于任务失败")
        app.swipeDown()
        app.swipeDown()
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "live-runtime-not-seen"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testRuntimeOnlineAgainstIsolatedBackend() {
        let app = launch()
        signOutIfNeeded(app)
        signIn(app)
        openPlanAndStartTask(app)

        XCTAssertTrue(app.staticTexts["后台执行程序最近有响应。"]
            .waitForExistence(timeout: 25), "应用没有读取到真实 online 状态")
        // 立即截图，避免后台 worker 处理完任务后提示按设计自动隐藏。
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "live-runtime-online"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
