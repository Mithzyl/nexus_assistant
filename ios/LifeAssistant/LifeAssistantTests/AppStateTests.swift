import XCTest
@testable import LifeAssistant

@MainActor
final class AppStateTests: XCTestCase {
    private func source(_ id: String) throws -> DemoSource {
        try XCTUnwrap(SampleData.demoSources.first { $0.id == id })
    }

    func testNoticeMovesStageWithoutClaimingArrival() throws {
        let state = AppState(demoMode: true)
        XCTAssertEqual(state.coffeeStage, .requested)

        _ = state.ingest(source: try source("notice"))

        XCTAssertEqual(state.coffeeStage, .merchantInitiated)
        XCTAssertNotEqual(state.coffeeStage, .received)
    }

    func testReceiptMarksReceivedAndUndoKeepsEvidence() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("receipt"))
        XCTAssertEqual(state.coffeeStage, .received)

        state.undoReceipt()
        XCTAssertEqual(state.coffeeStage, .merchantInitiated)
        XCTAssertTrue(state.receiptUndone)
    }

    func testRepeatedReceiptAfterUndoDoesNotReapply() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("receipt"))
        state.undoReceipt()

        let outcome = state.ingest(source: try source("receipt"))

        XCTAssertEqual(state.coffeeStage, .merchantInitiated)
        XCTAssertTrue(outcome.duplicate)
    }

    func testExplicitReapplyRestoresReceived() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("receipt"))
        state.undoReceipt()

        state.reapplyReceipt()

        XCTAssertEqual(state.coffeeStage, .received)
        XCTAssertFalse(state.receiptUndone)
    }

    func testLateNoticeDoesNotRollBackReceived() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("receipt"))
        _ = state.ingest(source: try source("notice"))

        XCTAssertEqual(state.coffeeStage, .received)
    }

    func testDuplicateOrderDoesNotCreateTwice() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("neworder"))
        _ = state.ingest(source: try source("neworder"))

        let lamps = state.matters.filter { $0.id == "demo-lamp" }
        XCTAssertEqual(lamps.count, 1)
    }

    func testAmbiguousNoticeWaitsForConfirmation() throws {
        let state = AppState(demoMode: true)
        let outcome = state.ingest(source: try source("ambiguous"))

        XCTAssertEqual(outcome.route, .ambiguity)
        XCTAssertTrue(state.pendingAmbiguity)
        XCTAssertEqual(state.coffeeStage, .requested)
        XCTAssertNil(state.ambiguityResolved)
    }

    func testConfirmingAmbiguityLinksWithoutChangingStage() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("ambiguous"))

        state.resolveAmbiguity("coffee")

        XCTAssertEqual(state.ambiguityResolved, "coffee")
        XCTAssertFalse(state.pendingAmbiguity)
        XCTAssertEqual(state.coffeeStage, .requested)
    }

    func testSpokenWishReusesExistingMatter() throws {
        let state = AppState(demoMode: true)
        let before = state.matters.count

        let outcome = state.ingest(text: "想去柳江边散散步")

        XCTAssertEqual(state.matters.count, before)
        XCTAssertEqual(outcome.route, .wish)
        XCTAssertTrue(outcome.duplicate)
    }

    func testDisablingMemoryRemovesCoatFromChecklist() throws {
        let state = AppState(demoMode: true)
        let before = state.activeChecklist.map(\.0)
        XCTAssertTrue(before.contains("coat"))

        state.toggleMemory("cinema")

        let after = state.activeChecklist.map(\.0)
        XCTAssertFalse(after.contains("coat"))
        XCTAssertTrue(after.contains("ticket"))
    }

    func testAskAnswersRefundFromStoredEvidence() throws {
        let state = AppState(demoMode: true)
        let answer = state.answer(for: "咖啡机退款到哪一步了？")

        XCTAssertTrue(answer.title.contains("到账"))
        XCTAssertFalse(answer.sources.isEmpty)
    }

    func testShelfStaysIndependentFromCoffee() throws {
        let state = AppState(demoMode: true)
        let coffee = try XCTUnwrap(state.matter("coffee"))
        let shelf = try XCTUnwrap(state.matter("shelf"))

        XCTAssertEqual(coffee.amount, shelf.amount)
        XCTAssertNotEqual(
            coffee.detail.first { $0.label == "订单号" }?.value,
            shelf.detail.first { $0.label == "订单号" }?.value
        )
        XCTAssertEqual(shelf.detail.first { $0.label == "订单号" }?.value, "DEMO-SHELF-1020")
        XCTAssertEqual(shelf.route, .shelf)
        XCTAssertEqual(state.shelfStage, .requested)
    }

    func testLinkingNoticeToShelfWaitsForArrival() throws {
        let state = AppState(demoMode: true)
        XCTAssertFalse(state.shelfInProgress)

        state.resolveAmbiguity("shelf")

        XCTAssertTrue(state.shelfInProgress)
        XCTAssertEqual(state.shelfStage, .requested)
        XCTAssertTrue(state.shelf?.subtitle.hasSuffix(" · 已关联新通知") == true)
    }

    func testCoffeeReceiptDoesNotChangeShelf() throws {
        let state = AppState(demoMode: true)
        _ = state.ingest(source: try source("receipt"))
        XCTAssertEqual(state.coffeeStage, .received)
        XCTAssertEqual(state.shelfStage, .requested)
        XCTAssertFalse(state.shelfInProgress)

        state.undoReceipt()
        XCTAssertEqual(state.shelfStage, .requested)
    }

    func testRelinkingNoticeToCoffeeClearsShelfLink() throws {
        let state = AppState(demoMode: true)
        state.resolveAmbiguity("shelf")
        XCTAssertTrue(state.shelfInProgress)

        state.resolveAmbiguity("coffee")

        XCTAssertFalse(state.shelfInProgress)
        XCTAssertEqual(state.shelfStage, .requested)
        XCTAssertFalse(state.shelf?.subtitle.contains("已关联新通知") == true)
        XCTAssertTrue(state.coffee?.subtitle.hasSuffix(" · 已关联新通知") == true)
    }

    func testAskRefusesToGuessWithoutEvidence() throws {
        let state = AppState(demoMode: true)
        let answer = state.answer(for: "我的护照什么时候到期？")

        XCTAssertTrue(answer.title.contains("没有找到依据"))
        XCTAssertTrue(answer.sources.isEmpty)
    }
}
