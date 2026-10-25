import XCTest

final class LoginPersistenceUITests: XCTestCase {
    func testLoginPersistsAcrossRelaunchOn8010() {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = "http://127.0.0.1:8010"
        app.launch()
        app.tabBars.buttons["我的"].tap()
        if !app.buttons["account.logout"].waitForExistence(timeout: 2) {
            XCTAssertTrue(app.buttons["account.login"].waitForExistence(timeout: 5))
            app.buttons["account.login"].tap()
            let email = app.textFields["login.email"]
            XCTAssertTrue(email.waitForExistence(timeout: 10))
            email.tap(); email.typeText("ui-a@example.com")
            let password = app.secureTextFields["login.password"]
            password.tap(); password.typeText("ui-password-1")
            app.buttons["login.submit"].tap()
            XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 15), "登录后没有进入已登录状态")
        }
        app.terminate()
        app.launch()
        app.tabBars.buttons["我的"].tap()
        XCTAssertTrue(app.buttons["account.logout"].waitForExistence(timeout: 15), "重启后登录状态丢失")
        add(XCTAttachment(screenshot: app.screenshot()))
    }
}
