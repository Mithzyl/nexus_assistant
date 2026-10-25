import XCTest

/// 助手页聊天顺序与滚动位置：最早在上、最新在下，进入页面停在最底。
/// 用 PA_DEMO=1 的演示仓库，不连真实服务；只验证界面顺序，不验证回答内容。
final class AskChatOrderUITests: XCTestCase {
    override func setUpWithError() throws {
        continueAfterFailure = false
    }

    func testChatRunsOldestToNewestAndStaysAtBottom() {
        let app = XCUIApplication()
        app.launchEnvironment["PA_DEMO"] = "1"
        app.launch()

        app.tabBars.buttons["助手"].tap()

        let seededQuestion = app.staticTexts["咖啡机退款到哪一步了？"]
        XCTAssertTrue(seededQuestion.waitForExistence(timeout: 20), "没有加载到历史问答")

        // 每次运行用不同问题，避免演示仓库重启后 id 复用造成的旧记录重名。
        let typedQuestion = "第二问 \(Int(Date().timeIntervalSince1970)) 要带伞吗？"
        let input = app.descendants(matching: .any)["ask.input"].firstMatch
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
        XCTAssertTrue(keyboardShown, "键盘没有出现，无法输入")
        input.typeText(typedQuestion)

        let send = app.buttons["ask.send"]
        XCTAssertTrue(send.waitForExistence(timeout: 5))
        let enabledBy = Date().addingTimeInterval(8)
        while !send.isEnabled && Date() < enabledBy {
            usleep(200_000)
        }
        XCTAssertTrue(send.isEnabled, "发送按钮仍禁用，输入可能没有进入输入框")
        send.tap()

        // 历史一条 + 新发一条，两条回答都来自演示仓库。
        let answers = app.descendants(matching: .any).matching(identifier: "ask.answer")
        XCTAssertTrue(waitForCount(answers, 2, timeout: 35), "两条回答没有都出现")

        let newQuestion = app.staticTexts[typedQuestion]
        XCTAssertTrue(newQuestion.waitForExistence(timeout: 10))
        XCTAssertLessThan(seededQuestion.frame.minY, newQuestion.frame.minY,
                          "旧问答应排在新的上面（从上往下按时间）")

        // Markdown 已经渲染：看不到原始标记，标题按组件显示。
        XCTAssertTrue(app.staticTexts["还差什么"].firstMatch.waitForExistence(timeout: 5), "标题没有按标题渲染")
        let visibleText = app.staticTexts.allElementsBoundByIndex.map(\.label).joined(separator: "\n")
        XCTAssertFalse(visibleText.contains("**"), "回答里仍有未渲染的加粗标记")
        XCTAssertFalse(visibleText.contains("## "), "回答里仍有未渲染的标题标记")
        XCTAssertFalse(visibleText.contains("> 收到回执"), "回答里仍有未渲染的引用标记")

        // 已滚到最底：底部说明完整露出，套在输入框上方。
        let footer = app.staticTexts["关于这版回答"].firstMatch
        XCTAssertTrue(footer.waitForExistence(timeout: 5))
        XCTAssertLessThan(footer.frame.maxY, input.frame.minY + 1,
                          "进入页面后应停在最底，底部说明不能被输入框盖住")
        XCTAssertLessThan(newQuestion.frame.minY, footer.frame.minY,
                          "最新问答应在底部说明之上")
        XCTAssertLessThan(answers.element(boundBy: 1).frame.minY, input.frame.minY,
                          "最新回答不能被输入框盖住")

        let shot = XCTAttachment(screenshot: XCUIScreen.main.screenshot())
        shot.name = "ask-chat-bottom"
        shot.lifetime = .keepAlways
        add(shot)
    }

    private func waitForCount(_ query: XCUIElementQuery, _ expected: Int, timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if query.count >= expected { return true }
            RunLoop.current.run(until: Date().addingTimeInterval(0.3))
        }
        return query.count >= expected
    }
}
