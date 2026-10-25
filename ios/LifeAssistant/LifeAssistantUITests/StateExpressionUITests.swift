import XCTest

/// 本轮界面验证：旧聊天核对、独立天气卡、真实处理阶段；全部使用演示仓库，不连真实服务。
final class StateExpressionUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testUnknownChatHidesBodyAndOffersVerify() {
        let app = XCUIApplication()
        app.launchEnvironment["PA_DEMO"] = "1"
        app.launchEnvironment["PA_DEMO_VERIFY"] = "unknown"
        app.launch()
        app.tabBars.buttons["助手"].tap()

        XCTAssertTrue(app.staticTexts["这条历史记录暂时无法确认"].firstMatch
            .waitForExistence(timeout: 25), "unknown 历史没有遮罩正文")
        XCTAssertFalse(app.staticTexts["咖啡机退款到哪一步了？"].exists,
                       "无法确认的记录不应展示未经确认的正文")
        XCTAssertTrue(app.buttons["ask.verifyAgain"].firstMatch.exists,
                      "unknown 应提供稍后核对入口")
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "chat-unknown"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testLatestWeatherCardIsSeparateFromAdvisory() {
        let app = XCUIApplication()
        app.launchEnvironment["COMMUTE_DEMO"] = "1"
        app.launchEnvironment["PA_DEMO"] = "1"
        app.launchEnvironment["START_ROUTE"] = "commuteAdvisory"
        app.launch()

        XCTAssertTrue(app.staticTexts["最新天气"].firstMatch.waitForExistence(timeout: 25),
                      "详情没有独立最新天气卡")
        XCTAssertTrue(app.staticTexts["通勤时段天气"].firstMatch.exists,
                      "建议采用的天气卡不应被最新天气覆盖")
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "latest-weather"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testWaitingShowsConfirmedStatusOnly() {
        let app = XCUIApplication()
        app.launchEnvironment["COMMUTE_DEMO"] = "1"
        app.launchEnvironment["COMMUTE_DEMO_CHECK"] = "queued"
        app.launchEnvironment["START_ROUTE"] = "commuteAdvisory"
        app.launch()

        let update = app.buttons["commute.updateRoute"]
        XCTAssertTrue(update.waitForExistence(timeout: 25))
        update.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "已提交")).firstMatch
            .waitForExistence(timeout: 20), "没有展示已确认的等待状态")
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "commute-waiting"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
