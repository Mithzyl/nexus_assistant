import XCTest
@testable import LifeAssistant

final class MarkdownParserTests: XCTestCase {
    func testParsesHeadingsAndParagraphs() {
        let blocks = MarkdownParser.parse("""
        # 标题

        第一段第一行
        第一段第二行

        ## 还差什么

        **商家已经发起退款**，但这条通知不是到账凭证。
        """)

        XCTAssertEqual(blocks, [
            .heading(level: 1, text: "标题"),
            .paragraph("第一段第一行\n第一段第二行"),
            .heading(level: 2, text: "还差什么"),
            .paragraph("**商家已经发起退款**，但这条通知不是到账凭证。")
        ])
    }

    func testParsesOrderedListWithNumbersAndNesting() {
        let blocks = MarkdownParser.parse("""
        1. 找到到账记录
        2. 把回执交给助手
          - 也可以先记下金额
        3) 再核对订单号
        """)

        XCTAssertEqual(blocks, [
            .ordered(number: 1, text: "找到到账记录", depth: 0),
            .ordered(number: 2, text: "把回执交给助手", depth: 0),
            .bullet(text: "也可以先记下金额", depth: 1),
            .ordered(number: 3, text: "再核对订单号", depth: 0)
        ])
    }

    func testParsesQuoteCodeRuleAndBullets() {
        let blocks = MarkdownParser.parse("""
        > 只凭金额不能关联

        ```swift
        let x = 1
        ```

        ---

        - 第一项
        * 第二项
        """)

        XCTAssertEqual(blocks, [
            .quote("只凭金额不能关联"),
            .code("let x = 1"),
            .rule,
            .bullet(text: "第一项", depth: 0),
            .bullet(text: "第二项", depth: 0)
        ])
    }

    func testInlineStripsBoldMarkersAndKeepsStrongIntent() {
        let attributed = MarkdownParser.inline("**商家已经发起退款**，但这条通知不是到账凭证。")

        XCTAssertEqual(String(attributed.characters), "商家已经发起退款，但这条通知不是到账凭证。")
        XCTAssertFalse(String(attributed.characters).contains("**"))
        XCTAssertTrue(attributed.runs.contains { $0.inlinePresentationIntent == .stronglyEmphasized },
                      "加粗没有变成强调属性")
    }

    func testInlineCodeIsParsedWithoutBackticks() {
        let attributed = MarkdownParser.inline("运行 `pytest -q` 即可")
        XCTAssertEqual(String(attributed.characters), "运行 pytest -q 即可")
    }

    func testPlainTextWithStrayAsteriskIsPreserved() {
        let blocks = MarkdownParser.parse("2 * 3 = 6")
        XCTAssertEqual(blocks, [.paragraph("2 * 3 = 6")])

        let attributed = MarkdownParser.inline("2 * 3 = 6")
        XCTAssertEqual(String(attributed.characters), "2 * 3 = 6")
    }

    func testEmptyAnswerHasNoBlocks() {
        XCTAssertTrue(MarkdownParser.parse("").isEmpty)
        XCTAssertTrue(MarkdownParser.parse("   \n  ").isEmpty)
    }
}
