import XCTest

/// 真实 PA 后端（127.0.0.1:8011）+ nexus-stub（8012）。
/// 不使用 PA_DEMO；登录、发送、查询、取消、重启恢复和换账号都走真实 HTTP。
@MainActor
final class PAEndToEndUITests: XCTestCase {
    private let baseURL = "http://127.0.0.1:8011"
    private let accountA = ("ui-a@example.com", "ui-password-1")
    private let accountB = ("ui-b@example.com", "ui-password-1")

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testSecondSendAfterRelaunchCompletes() async throws {
        try await resetStub()
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL
        app.launch()
        ensureSignedOut(app)
        login(app, email: accountA.0, password: accountA.1)

        send(app, "first question after login")
        XCTAssertTrue(answerElement(app).waitForExistence(timeout: 40))

        app.terminate()
        app.launch()
        app.tabBars.buttons["助手"].tap()
        XCTAssertTrue(answerElement(app).waitForExistence(timeout: 30))
        sleep(2)

        send(app, "second question after relaunch")
        XCTAssertTrue(app.staticTexts["second question after relaunch"].waitForExistence(timeout: 15),
                      "第二次发送没有进入问答列表")
        let answers = app.descendants(matching: .any).matching(identifier: "ask.answer")
        let deadline = Date().addingTimeInterval(40)
        while answers.count < 3 && Date() < deadline {
            usleep(500_000)
        }
        XCTAssertGreaterThanOrEqual(answers.count, 3, "第二次发送没有生成新的已完成回答")
    }

    func testRunningAskCanBeStoppedThenNewAskCompletes() async throws {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL
        app.launch()
        try await resetStub()
        ensureSignedOut(app)
        login(app, email: accountA.0, password: accountA.1)
        try await setHold(true)

        send(app, "hold this request until stopped")
        let cancel = app.buttons["ask.cancel"].firstMatch
        XCTAssertTrue(cancel.waitForExistence(timeout: 20), "没有出现可停止的运行")
        cancel.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "已取消")
        ).firstMatch.waitForExistence(timeout: 30), "没有显示服务器确认的取消")
        try await releaseAll()
        sleep(3)
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "已取消")
        ).firstMatch.exists, "迟到的终态把已取消复活了")

        send(app, "new request after stopping")
        XCTAssertTrue(app.staticTexts["new request after stopping"].waitForExistence(timeout: 15))
        try await releaseAll()
        let answers = app.descendants(matching: .any).matching(identifier: "ask.answer")
        let deadline = Date().addingTimeInterval(40)
        while answers.count < 2 && Date() < deadline { usleep(500_000) }
        XCTAssertGreaterThanOrEqual(answers.count, 2, "停止后新问题没有完成")
    }

    func testAccountSwitchHidesLateResponse() async throws {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL
        app.launch()
        try await resetStub()
        ensureSignedOut(app)
        login(app, email: accountA.0, password: accountA.1)
        try await setHold(true)
        send(app, "stale account A response")

        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 10))
        login(app, email: accountB.0, password: accountB.1)
        try await releaseAll()
        sleep(8)

        app.tabBars.buttons["助手"].tap()
        let stale = app.descendants(matching: .any).matching(
            NSPredicate(format: "label CONTAINS %@", "stale account A response")
        ).firstMatch
        XCTAssertFalse(stale.exists, "换账号后仍显示 A 的问题")

        try await setHold(false)
        send(app, "account B own question")
        XCTAssertTrue(app.staticTexts["account B own question"].waitForExistence(timeout: 15))
        XCTAssertTrue(answerElement(app).waitForExistence(timeout: 40),
                      "换账号后 B 自己无法得到回答")
    }

    // MARK: - Test control

    private func stubRequest(_ path: String) async throws {
        var request = URLRequest(url: URL(string: "http://127.0.0.1:8012" + path)!)
        request.httpMethod = "POST"
        let (_, response) = try await URLSession.shared.data(for: request)
        XCTAssertEqual((response as? HTTPURLResponse)?.statusCode, 200)
    }

    private func resetStub() async throws {
        try await stubRequest("/_debug/hold?enabled=false")
        try await stubRequest("/_debug/release")
    }

    private func setHold(_ enabled: Bool) async throws {
        try await stubRequest("/_debug/hold?enabled=\(enabled)")
    }

    private func releaseAll() async throws {
        try await stubRequest("/_debug/release")
    }

    // MARK: - Helpers

    private func answerElement(_ app: XCUIApplication) -> XCUIElement {
        app.descendants(matching: .any).matching(identifier: "ask.answer").firstMatch
    }

    private func ensureSignedOut(_ app: XCUIApplication) {
        app.tabBars.buttons["我的"].tap()
        if app.buttons["account.logout"].waitForExistence(timeout: 5) {
            app.buttons["account.logout"].tap()
            confirmLogout(app)
        }
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 10),
                      "无法回到未登录状态")
    }

    private func confirmLogout(_ app: XCUIApplication) {
        let sheetButton = app.sheets.buttons["退出登录"]
        if sheetButton.waitForExistence(timeout: 3) {
            sheetButton.tap()
            return
        }
        let alertButton = app.alerts.buttons["退出登录"]
        if alertButton.exists {
            alertButton.tap()
            return
        }
        let candidates = app.buttons.matching(identifier: "退出登录")
        if candidates.count > 0 {
            candidates.element(boundBy: candidates.count - 1).tap()
        }
    }

    private func login(_ app: XCUIApplication, email: String, password: String) {
        app.tabBars.buttons["我的"].tap()
        let loginButton = app.buttons["account.login"]
        XCTAssertTrue(loginButton.waitForExistence(timeout: 10))
        loginButton.tap()

        let emailField = app.textFields["login.email"]
        XCTAssertTrue(emailField.waitForExistence(timeout: 10))
        emailField.tap()
        emailField.typeText(email)

        let passwordField = app.secureTextFields["login.password"]
        XCTAssertTrue(passwordField.waitForExistence(timeout: 5))
        passwordField.tap()
        passwordField.typeText(password)

        app.buttons["login.submit"].tap()
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 20),
                      "登录后没有进入已登录状态")
        // 等登录 sheet 完成离场，避免紧接着的 tab 切换被吞掉。
        let sheetGone = XCTNSPredicateExpectation(
            predicate: NSPredicate(format: "exists == false"),
            object: app.buttons["login.submit"]
        )
        _ = XCTWaiter.wait(for: [sheetGone], timeout: 10)
        sleep(1)
    }

    private func send(_ app: XCUIApplication, _ text: String) {
        app.tabBars.buttons["助手"].tap()
        let input = app.descendants(matching: .any)["ask.input"].firstMatch
        if !input.waitForExistence(timeout: 5) {
            app.tabBars.buttons["助手"].tap()
        }
        XCTAssertTrue(input.waitForExistence(timeout: 10))
        var keyboardShown = false
        for attempt in 0..<4 {
            if attempt == 0 {
                input.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
            } else {
                input.tap()
            }
            if app.keyboards.element.waitForExistence(timeout: 2) {
                keyboardShown = true
                break
            }
            usleep(300_000)
        }
        XCTAssertTrue(keyboardShown && app.keyboards.element.exists, "键盘没有出现，无法输入")
        input.typeText(text)
        let send = app.buttons["ask.send"]
        XCTAssertTrue(send.waitForExistence(timeout: 5))
        let deadline = Date().addingTimeInterval(8)
        while !send.isEnabled && Date() < deadline {
            usleep(200_000)
        }
        XCTAssertTrue(send.isEnabled, "发送按钮仍禁用，输入可能没有进入输入框")
        send.tap()
    }
}
