import XCTest

/// 合成笔记全路径：粘贴 → 预览 → 保存 → 重开 → 重启 → 切号不可见 → 切回恢复。
final class NoteImportIsolationUITests: XCTestCase {
    private let baseURL = "http://127.0.0.1:8123"
    private let accountA = ("ui-a@example.com", "ui-password-1")
    private let accountB = ("ui-b@example.com", "ui-password-1")
    private let noteTitle = "Synthetic note A"
    private let noteBody = "Synthetic body for account A only"

    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testNoteImportPersistsAndIsIsolatedByAccount() {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = baseURL
        app.launch()

        ensureSignedOut(app)
        login(app, email: accountA.0, password: accountA.1)
        openNotes(app)

        app.buttons["notes.paste"].tap()
        let title = app.textFields["notes.title"]
        XCTAssertTrue(title.waitForExistence(timeout: 10))
        title.tap()
        title.typeText(noteTitle)
        let body = app.textViews["notes.body"]
        XCTAssertTrue(body.waitForExistence(timeout: 5))
        body.tap()
        body.typeText(noteBody)
        app.buttons["notes.save"].tap()

        XCTAssertTrue(app.staticTexts[noteTitle].waitForExistence(timeout: 10), "保存后没有出现笔记")

        // 退出详情再打开
        app.buttons["nav.back"].tap()
        app.buttons["sources.notes"].tap()
        XCTAssertTrue(app.staticTexts[noteTitle].waitForExistence(timeout: 10), "重新进入后笔记丢失")

        // 重启恢复
        app.terminate()
        app.launch()
        openNotes(app)
        XCTAssertTrue(app.staticTexts[noteTitle].waitForExistence(timeout: 15), "重启后笔记丢失")

        // 切到 B：不可见
        goBackToRoot(app)
        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 10))
        login(app, email: accountB.0, password: accountB.1)
        openNotes(app)
        XCTAssertFalse(app.staticTexts[noteTitle].exists, "B 看到了 A 的笔记")

        // 切回 A：恢复
        goBackToRoot(app)
        app.tabBars.buttons["我的"].tap()
        app.buttons["account.logout"].tap()
        confirmLogout(app)
        login(app, email: accountA.0, password: accountA.1)
        openNotes(app)
        XCTAssertTrue(app.staticTexts[noteTitle].waitForExistence(timeout: 10), "切回 A 后笔记没有恢复")
    }

    // MARK: - Helpers

    /// 详情页是 NavigationStack push，先退回根页面才有 TabBar。
    private func goBackToRoot(_ app: XCUIApplication) {
        for _ in 0..<3 {
            let back = app.buttons["nav.back"]
            guard back.exists else { break }
            back.tap()
        }
    }

    private func openNotes(_ app: XCUIApplication) {
        app.tabBars.buttons["我的"].tap()
        let entry = app.buttons["me.dataSources"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        let notes = app.buttons["sources.notes"]
        XCTAssertTrue(notes.waitForExistence(timeout: 10))
        notes.tap()
        XCTAssertTrue(app.staticTexts["把需要的笔记带进来"].waitForExistence(timeout: 10))
    }

    private func ensureSignedOut(_ app: XCUIApplication) {
        app.tabBars.buttons["我的"].tap()
        if app.buttons["account.logout"].waitForExistence(timeout: 5) {
            app.buttons["account.logout"].tap()
            confirmLogout(app)
        }
        XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 10))
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
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 20))
        dismissPasswordPrompt(app)
        sleep(1)
    }

    /// 登录后可能弹出“保存密码？”系统弹层，先收起再继续点击。
    private func dismissPasswordPrompt(_ app: XCUIApplication) {
        let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
        for candidate in [app.buttons["以后"], springboard.buttons["以后"]] {
            if candidate.waitForExistence(timeout: 2) {
                candidate.tap()
                return
            }
        }
    }
}
