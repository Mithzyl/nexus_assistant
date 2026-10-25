import XCTest

/// 后台运行状态：等待、近期未响应、请求失败、在线；均使用演示仓库，不连真实服务。
final class RuntimeStatusUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    private func launch(_ scenario: String,
                        seenAgo: String? = nil,
                        staleAfter: String? = nil,
                        sequence: String? = nil) -> XCUIApplication {
        let app = XCUIApplication()
        app.launchEnvironment["PA_DEMO"] = "1"
        app.launchEnvironment["COMMUTE_DEMO"] = "1"
        app.launchEnvironment["COMMUTE_DEMO_CHECK"] = "queued"
        app.launchEnvironment["COMMUTE_DEMO_RUNTIME"] = scenario
        if let seenAgo { app.launchEnvironment["COMMUTE_DEMO_SEEN_AGO"] = seenAgo }
        if let staleAfter { app.launchEnvironment["COMMUTE_DEMO_STALE_AFTER"] = staleAfter }
        if let sequence { app.launchEnvironment["COMMUTE_DEMO_RUNTIME_SEQUENCE"] = sequence }
        app.launchEnvironment["START_ROUTE"] = "commuteAdvisory"
        app.launch()
        return app
    }

    private func startWaitingTask(_ app: XCUIApplication) {
        let update = app.buttons["commute.updateRoute"]
        XCTAssertTrue(update.waitForExistence(timeout: 25), "演示详情没有分析入口")
        update.tap()
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch
            .waitForExistence(timeout: 40), "没有先显示任务已保存/等待状态")
    }

    func testNotSeenExplainsNoWorkerResponse() {
        let app = launch("not_seen")
        startWaitingTask(app)
        XCTAssertTrue(app.staticTexts["尚未收到后台执行程序的响应。"]
            .waitForExistence(timeout: 20), "没有显示未报到说明")
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "任务仍已保存")).firstMatch.exists)
        app.swipeDown()
        app.swipeDown()
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "runtime-not-seen"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testStaleExplainsRecentNoResponseWithoutFailure() {
        let app = launch("stale")
        startWaitingTask(app)
        XCTAssertTrue(app.staticTexts["后台执行程序近期未响应，任务仍已保存。"]
            .waitForExistence(timeout: 20), "没有显示近期未响应说明")
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上次分析失败")).firstMatch.exists,
            "心跳过期不能显示成任务失败")
        app.swipeDown()
        app.swipeDown()
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "runtime-stale"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testOnlineDoesNotTurnQueuedIntoAnalysis() {
        let app = launch("online")
        startWaitingTask(app)
        XCTAssertTrue(app.staticTexts["后台执行程序最近有响应。"]
            .waitForExistence(timeout: 20), "没有显示在线说明")
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "正在分析建议")).firstMatch.exists,
            "心跳新鲜不能把排队任务显示成分析中")
    }


    func testOnlineHintAgesToStaleWhilePageStaysOpen() {
        let app = launch("online", seenAgo: "1", staleAfter: "3")
        startWaitingTask(app)
        XCTAssertTrue(app.staticTexts["后台执行程序最近有响应。"]
            .waitForExistence(timeout: 20), "没有先显示在线")

        XCTAssertTrue(app.staticTexts["后台执行程序近期未响应，任务仍已保存。"]
            .waitForExistence(timeout: 15), "页面停留后没有按经过时长改为近期未响应")
        XCTAssertFalse(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上次分析失败")).firstMatch.exists)
        app.swipeDown()
        app.swipeDown()
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "runtime-aged-stale"
        shot.lifetime = .keepAlways
        add(shot)
    }

    func testReturningToDetailReadsUpdatedRuntime() {
        let app = launch("online", seenAgo: "1", staleAfter: "60",
                         sequence: "online,stale")
        startWaitingTask(app)
        XCTAssertTrue(app.staticTexts["后台执行程序最近有响应。"]
            .waitForExistence(timeout: 20))

        app.buttons["nav.back"].tap()
        let card = app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "上班通勤")).firstMatch
        XCTAssertTrue(card.waitForExistence(timeout: 20))
        card.tap()

        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch
            .waitForExistence(timeout: 20))
        XCTAssertTrue(app.staticTexts["后台执行程序近期未响应，任务仍已保存。"]
            .waitForExistence(timeout: 20), "回到详情没有读取更新的后台状态")
    }

    func testReopenRestoresSameWaitingTask() {
        let app = launch("not_seen")
        startWaitingTask(app)

        app.terminate()
        // 演示仓库默认不恢复待处理记录；这里显式验证“重开继续同一检查”。
        app.launchEnvironment["COMMUTE_DEMO_KEEP_PENDING"] = "1"
        app.launch()

        XCTAssertTrue(app.buttons["commute.updateRoute"].waitForExistence(timeout: 25))
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch
            .waitForExistence(timeout: 20), "重开没有恢复任务说明")
        XCTAssertFalse(app.buttons["commute.updateRoute"].isEnabled,
                       "重开应恢复原来的待处理任务，而不是允许新建")
        XCTAssertTrue(app.staticTexts["尚未收到后台执行程序的响应。"]
            .waitForExistence(timeout: 20), "重开没有恢复后台状态说明")
    }

    func testRuntimeRequestFailureKeepsTask() {
        let app = launch("error")
        startWaitingTask(app)
        XCTAssertTrue(app.staticTexts["暂时无法获取后台状态。"]
            .waitForExistence(timeout: 20), "没有显示无法获取后台状态")
        XCTAssertTrue(app.staticTexts.matching(
            NSPredicate(format: "label CONTAINS %@", "等待服务器处理")).firstMatch.exists,
            "读取心跳失败不能取消或重提任务")
        app.swipeDown()
        app.swipeDown()
        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "runtime-error"
        shot.lifetime = .keepAlways
        add(shot)
    }
}
