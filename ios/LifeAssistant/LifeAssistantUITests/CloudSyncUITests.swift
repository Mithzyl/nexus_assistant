import XCTest

final class CloudSyncUITests: XCTestCase {
    func testCreateSourceAndUploadFromApp() {
        let app = XCUIApplication()
        app.launchEnvironment["PA_API_BASE_URL"] = "http://127.0.0.1:8010"
        app.launchEnvironment["START_ROUTE"] = "dataSources"
        app.launchEnvironment["START_CLOUD"] = "1"
        app.launch()
        XCTAssertTrue(app.buttons["cloud.create"].waitForExistence(timeout: 10))
        let existing = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "粘贴备忘录")).firstMatch
        if !existing.exists {
            app.buttons["cloud.create"].tap()
            XCTAssertTrue(app.buttons["备忘录"].waitForExistence(timeout: 5))
            app.buttons["备忘录"].tap()
            app.buttons["cloud.create.submit"].tap()
            XCTAssertTrue(app.staticTexts["粘贴备忘录"].waitForExistence(timeout: 15), "新建来源未出现在列表")
        }
        let source = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "粘贴备忘录")).firstMatch
        XCTAssertTrue(source.waitForExistence(timeout: 10))
        source.tap()
        XCTAssertTrue(app.buttons["cloud.save"].waitForExistence(timeout: 10))
        app.buttons["cloud.save"].tap()
        app.swipeUp()
        XCTAssertTrue(app.buttons["cloud.upload"].waitForExistence(timeout: 10))
        app.buttons["cloud.upload"].tap()
        XCTAssertTrue(app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "同步完成")).firstMatch.waitForExistence(timeout: 20), "上传未完成")
        add(XCTAttachment(screenshot: app.screenshot()))
    }
}
