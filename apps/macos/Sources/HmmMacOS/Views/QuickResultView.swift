import Foundation
import SwiftUI

struct QuickResultView: View {
    let event: ListeningEventSummary?
    var recentEvents: [ListeningEventSummary] = []
    var pinnedEvents: [ListeningEventSummary] = []
    var historyPersistent = false
    var historyActionMessage: String?
    var conversation: ConversationAskResponse?
    var isAskingConversation = false
    var generation: GenerationRecord?
    var generationHistory: GenerationHistoryResponse?
    var generationActionMessage: String?
    var isCreatingGenerationPrompt = false
    var comparison: RouteComparisonSummary?
    var isRerunning = false
    var onRerun: (String) -> Void = { _ in }
    var onAskConversation: (String) -> Void = { _ in }
    var onCreateGenerationPrompt: (String?) -> Void = { _ in }
    var onRefreshGenerationHistory: () -> Void = {}
    var onSelectRecent: (ListeningEventSummary) -> Void = { _ in }
    var onTogglePinned: (ListeningEventSummary, Bool) -> Void = { _, _ in }
    var onBatchPinned: ([String], Bool) -> Void = { _, _ in }
    var onClearHistory: (Bool) -> Void = { _ in }
    var onExportHistory: () -> Void = {}
    var onArchiveHistory: ([String]) -> Void = { _ in }
    @State private var historyRouteFilter = "all"
    @State private var historySourceFilter = "all"
    @State private var historyRerunnableOnly = false
    @State private var historyReviewMode = false
    @State private var selectedHistoryIds = Set<String>()
    @State private var comparisonChangedOnly = false
    @State private var comparisonMinDelta = 0.0
    @State private var conversationQuestion = "What is happening in this sound?"
    @State private var generationPromptDraft = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Quick Result")
                    .font(.headline)
                Spacer()
                if let policy = event?.rawAudioPolicy {
                    Text(policy)
                        .font(.caption)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(.secondary.opacity(0.12), in: Capsule())
                }
            }

            if let event {
                VStack(alignment: .leading, spacing: 8) {
                    Text(event.aggregate?.title ?? "Listening event")
                        .font(.title3.weight(.semibold))
                    Text(event.aggregate?.shortSummary ?? "No compact summary was returned.")
                        .foregroundStyle(.secondary)

                    if let tags = event.aggregate?.primaryTags, !tags.isEmpty {
                        HStack {
                            ForEach(tags.prefix(5), id: \.self) { tag in
                                Text(tag)
                                    .font(.caption)
                                    .padding(.horizontal, 8)
                                    .padding(.vertical, 4)
                                    .background(.tertiary.opacity(0.22), in: Capsule())
                            }
                        }
                    }

                    if let memory = event.memory, let matches = memory.similarTraceIds, !matches.isEmpty {
                        Label("\(matches.count) memory match\(matches.count == 1 ? "" : "es")", systemImage: "link")
                            .foregroundStyle(.green)
                    }

                    routeActions(for: event)
                    conversationView
                    generationView
                    comparisonView
                    recentHistoryView

                    Text(compactTimestamp(event.createdAt))
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                }
            } else {
                VStack(spacing: 8) {
                    Image(systemName: "waveform")
                        .font(.largeTitle)
                        .foregroundStyle(.secondary)
                    Text("No result yet")
                        .font(.headline)
                    Text("Start a live session in the dashboard, then use Capture Buffer here or from the menu bar.")
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, minHeight: 120)
                recentHistoryView
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.background, in: RoundedRectangle(cornerRadius: 8, style: .continuous))
    }

    @ViewBuilder
    private var conversationView: some View {
        if event != nil {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    TextField("Question", text: $conversationQuestion)
                        .textFieldStyle(.roundedBorder)
                        .onSubmit {
                            onAskConversation(conversationQuestion)
                        }

                    Button {
                        onAskConversation(conversationQuestion)
                    } label: {
                        Label(isAskingConversation ? "Asking" : "Ask", systemImage: "text.bubble")
                    }
                    .disabled(isAskingConversation || conversationQuestion.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }

                if let turn = conversation?.turn {
                    VStack(alignment: .leading, spacing: 7) {
                        Text(turn.answer ?? "")
                            .font(.callout)
                            .foregroundStyle(.primary)

                        if let facts = turn.knownFacts, !facts.isEmpty {
                            conversationChips("Known", facts.prefix(3).map { $0 })
                        }

                        if let hypotheses = turn.hypotheses, !hypotheses.isEmpty {
                            conversationChips("Hypotheses", hypotheses.prefix(2).map { $0 })
                        }

                        if let memory = turn.memoryContext, !memory.isEmpty {
                            conversationChips("Memory", memory.prefix(2).map { item in
                                if let score = item.score {
                                    return "\(item.title ?? item.traceId ?? "trace") \(Int(round(score * 100)))%"
                                }
                                return item.title ?? item.traceId ?? "trace"
                            })
                        }

                        if let notes = turn.uncertaintyNotes, !notes.isEmpty {
                            conversationChips("Uncertainty", notes.prefix(2).map { $0 })
                        }
                    }
                    .padding(10)
                    .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
                }
            }
        }
    }

    private func conversationChips(_ label: String, _ values: [String]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
            HStack(spacing: 6) {
                ForEach(Array(values.enumerated()), id: \.offset) { _, value in
                    Text(shortLabel(value, maxLength: 46))
                        .font(.caption2)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 3)
                        .background(.background.opacity(0.7), in: Capsule())
                }
            }
        }
    }

    @ViewBuilder
    private var generationView: some View {
        if event != nil {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("Generative bridge", systemImage: "sparkles")
                        .font(.subheadline.weight(.semibold))
                    Spacer()

                    Button {
                        onCreateGenerationPrompt(nil)
                    } label: {
                        Label(isCreatingGenerationPrompt ? "Prompting" : "Prompt", systemImage: "sparkles")
                    }
                    .disabled(isCreatingGenerationPrompt)

                    Button {
                        onCreateGenerationPrompt(generationPromptDraft)
                    } label: {
                        Label("Save", systemImage: "square.and.arrow.down")
                    }
                    .disabled(isCreatingGenerationPrompt || generationPromptDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)

                    Button {
                        onRefreshGenerationHistory()
                    } label: {
                        Image(systemName: "clock.arrow.circlepath")
                    }
                    .help("Prompt history")
                }
                .buttonStyle(.bordered)

                TextEditor(text: $generationPromptDraft)
                    .font(.system(.callout, design: .monospaced))
                    .frame(minHeight: 74, maxHeight: 110)
                    .padding(6)
                    .background(.background.opacity(0.7), in: RoundedRectangle(cornerRadius: 6, style: .continuous))

                HStack(spacing: 8) {
                    if let generation {
                        Text(generation.status ?? "prompt_ready")
                            .font(.caption.weight(.semibold))
                        Text(generation.adapter ?? "prompt_only")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                        if let sourceEventId = generation.sourceEventId {
                            Text(shortLabel(sourceEventId, maxLength: 22))
                                .font(.caption2)
                                .foregroundStyle(.tertiary)
                        }
                    }

                    if let generationActionMessage {
                        Text(generationActionMessage)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }

                if let records = generationHistory?.records, !records.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(records.prefix(3)) { record in
                            Text(shortLabel(record.prompt ?? record.id, maxLength: 42))
                                .font(.caption2)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 3)
                                .background(.background.opacity(0.7), in: Capsule())
                        }
                    }
                }
            }
            .padding(10)
            .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            .task(id: generation?.id) {
                generationPromptDraft = generation?.prompt ?? ""
            }
            .task(id: event?.id) {
                if generation == nil {
                    generationPromptDraft = ""
                }
            }
        }
    }

    @ViewBuilder
    private func routeActions(for event: ListeningEventSummary) -> some View {
        let actions = (event.aggregate?.nextActions ?? [])
            .filter { $0.routePreset != nil && $0.id != "remember" }
        if !actions.isEmpty {
            HStack(spacing: 8) {
                ForEach(actions.prefix(4)) { action in
                    Button {
                        if let preset = action.routePreset {
                            onRerun(preset)
                        }
                    } label: {
                        Label(shortRouteLabel(action), systemImage: "arrow.triangle.2.circlepath")
                    }
                    .disabled(isRerunning)
                    .help(action.label ?? "Run route")
                }
            }
            .buttonStyle(.bordered)
        }
    }

    @ViewBuilder
    private var comparisonView: some View {
        if let comparison {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label("Route comparison", systemImage: "arrow.triangle.2.circlepath")
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text(comparison.sameSegment == true ? "same segment" : "segment uncertain")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 8) {
                    let metrics = comparisonMetrics(comparison)
                    if metrics.isEmpty {
                        Text("No changed fields match the active filters.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(metrics, id: \.title) { metric in
                            comparisonPill(metric.title, metric.value)
                        }
                    }
                }

                HStack(spacing: 12) {
                    Toggle("Changed only", isOn: $comparisonChangedOnly)
                        .toggleStyle(.checkbox)
                    Stepper("DSP >= \(String(format: "%.1f", comparisonMinDelta))", value: $comparisonMinDelta, in: 0...24, step: 0.5)
                }
                .font(.caption)

                let deltas = filteredSignalDeltas(comparison).prefix(3)
                if !deltas.isEmpty {
                    HStack(spacing: 6) {
                        ForEach(Array(deltas.enumerated()), id: \.offset) { _, delta in
                            Text("\(delta.label ?? "Signal") \(signedDelta(delta.delta))")
                                .font(.caption)
                                .padding(.horizontal, 7)
                                .padding(.vertical, 3)
                                .background(.secondary.opacity(0.10), in: Capsule())
                        }
                    }
                }
            }
            .padding(10)
            .background(.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        }
    }

    @ViewBuilder
    private var recentHistoryView: some View {
        if !recentEvents.isEmpty || !pinnedEvents.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Text("Recent results")
                        .font(.subheadline.weight(.semibold))
                    Spacer()
                    Text("\(recentEvents.count) kept / \(pinnedEvents.count) pinned \(historyPersistent ? "persistently" : "in memory")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 8) {
                    Button {
                        onExportHistory()
                    } label: {
                        Label("Export", systemImage: "square.and.arrow.up")
                    }

                    Button {
                        onArchiveHistory(historyReviewMode ? Array(selectedHistoryIds) : [])
                    } label: {
                        Label("Archive", systemImage: "archivebox")
                    }

                    if let event {
                        let pinned = isEventPinned(event)
                        Button {
                            onTogglePinned(event, !pinned)
                        } label: {
                            Label(pinned ? "Unpin" : "Pin", systemImage: pinned ? "pin.slash" : "pin")
                        }
                    }

                    Button {
                        onClearHistory(true)
                    } label: {
                        Label("Clear", systemImage: "clock.arrow.circlepath")
                    }
                    .disabled(recentEvents.isEmpty)

                    Button {
                        onClearHistory(false)
                    } label: {
                        Label("Clear All", systemImage: "trash")
                    }
                    .disabled(recentEvents.isEmpty && pinnedEvents.isEmpty)

                    Button {
                        historyReviewMode.toggle()
                    } label: {
                        Label(historyReviewMode ? "Done" : "Review", systemImage: "checklist")
                    }
                }
                .buttonStyle(.bordered)
                .font(.caption)

                if let historyActionMessage {
                    Text(historyActionMessage)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }

                HStack(spacing: 8) {
                    Picker("Route", selection: $historyRouteFilter) {
                        Text("All routes").tag("all")
                        ForEach(historyRouteOptions, id: \.self) { route in
                            Text(shortLabel(route, maxLength: 18)).tag(route)
                        }
                    }
                    .frame(maxWidth: 170)

                    Picker("Source", selection: $historySourceFilter) {
                        Text("All sources").tag("all")
                        ForEach(historySourceOptions, id: \.self) { source in
                            Text(displayStatus(source)).tag(source)
                        }
                    }
                    .frame(maxWidth: 170)

                    Toggle("Rerunnable", isOn: $historyRerunnableOnly)
                        .toggleStyle(.checkbox)
                }
                .font(.caption)

                if historyReviewMode {
                    reviewControls
                }

                let visiblePinned = filteredPinnedEvents
                if !visiblePinned.isEmpty {
                    Text("Pinned")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.secondary)

                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(visiblePinned.prefix(8)) { recent in
                                historyChip(recent, pinned: true)
                            }
                        }
                    }
                }

                let visibleRecent = filteredRecentEvents
                if !visibleRecent.isEmpty {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 8) {
                            ForEach(visibleRecent.prefix(8)) { recent in
                                historyChip(recent, pinned: isEventPinned(recent))
                            }
                        }
                    }
                }

                if visiblePinned.isEmpty && visibleRecent.isEmpty {
                    Text("No recent results match these filters.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
        }
    }

    private var reviewControls: some View {
        HStack(spacing: 8) {
            Text("\(selectedHistoryIds.count) selected")
                .font(.caption)
                .foregroundStyle(.secondary)

            Button {
                selectedHistoryIds.formUnion(shownHistoryEventIds)
            } label: {
                Label("Select Shown", systemImage: "checkmark.circle")
            }

            Button {
                selectedHistoryIds.removeAll()
            } label: {
                Label("Clear Selection", systemImage: "xmark.circle")
            }
            .disabled(selectedHistoryIds.isEmpty)

            Button {
                onBatchPinned(Array(selectedHistoryIds), true)
            } label: {
                Label("Pin Selected", systemImage: "pin")
            }
            .disabled(selectedHistoryIds.isEmpty)

            Button {
                onBatchPinned(Array(selectedHistoryIds), false)
            } label: {
                Label("Unpin Selected", systemImage: "pin.slash")
            }
            .disabled(selectedHistoryIds.isEmpty)

            Button {
                onArchiveHistory(Array(selectedHistoryIds))
            } label: {
                Label("Archive Selected", systemImage: "archivebox")
            }
            .disabled(selectedHistoryIds.isEmpty)
        }
        .buttonStyle(.bordered)
        .font(.caption)
    }

    private func historyChip(_ recent: ListeningEventSummary, pinned: Bool) -> some View {
        HStack(spacing: 0) {
            if historyReviewMode {
                Toggle(isOn: historySelectionBinding(for: recent.id)) {
                    EmptyView()
                }
                .toggleStyle(.checkbox)
                .labelsHidden()
                .padding(.trailing, 4)
            }

            Button {
                onSelectRecent(recent)
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(shortLabel(recent.aggregate?.title ?? "Listening event", maxLength: 28))
                        .font(.caption.weight(.semibold))
                    Text(recentMeta(recent))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                .frame(width: 150, alignment: .leading)
            }
            .buttonStyle(.bordered)

            Button {
                onTogglePinned(recent, !pinned)
            } label: {
                Image(systemName: pinned ? "pin.slash" : "pin")
                    .frame(width: 18, height: 18)
            }
            .buttonStyle(.bordered)
            .help(pinned ? "Unpin recent result" : "Pin recent result")
        }
    }

    private func historySelectionBinding(for eventId: String) -> Binding<Bool> {
        Binding(
            get: { selectedHistoryIds.contains(eventId) },
            set: { selected in
                if selected {
                    selectedHistoryIds.insert(eventId)
                } else {
                    selectedHistoryIds.remove(eventId)
                }
            }
        )
    }

    private func comparisonPill(_ title: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value)
                .font(.caption.weight(.semibold))
                .lineLimit(1)
            Text(title)
                .font(.caption2)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(.background.opacity(0.7), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
    }

    private func shortRouteLabel(_ action: ListeningNextActionSummary) -> String {
        if let preset = action.routePreset {
            return preset.replacingOccurrences(of: "-", with: " ").capitalized
        }
        return "Route"
    }

    private func routeChangeLabel(_ comparison: RouteComparisonSummary) -> String {
        let added = comparison.addedRoutes ?? []
        let removed = comparison.removedRoutes ?? []
        if let first = added.first {
            return "+\(shortLabel(first, maxLength: 14))"
        }
        if let first = removed.first {
            return "-\(shortLabel(first, maxLength: 14))"
        }
        return "same"
    }

    private func comparisonMetrics(_ comparison: RouteComparisonSummary) -> [(title: String, value: String, changed: Bool)] {
        let warningAdded = comparison.warningDelta?.added?.count ?? 0
        let warningResolved = comparison.warningDelta?.resolved?.count ?? 0
        let values: [(title: String, value: String, changed: Bool)] = [
            ("Routes", routeChangeLabel(comparison), comparison.changeFlags?.routesChanged == true),
            ("Summary", comparison.summaryShift?.changed == true ? "changed" : "unchanged", comparison.summaryShift?.changed == true),
            ("Warnings +", "\(warningAdded)", warningAdded > 0),
            ("Warnings -", "\(warningResolved)", warningResolved > 0),
        ]
        return values.filter { !comparisonChangedOnly || $0.changed }
    }

    private func filteredSignalDeltas(_ comparison: RouteComparisonSummary) -> [RouteSignalDelta] {
        let minimum = comparisonChangedOnly ? max(comparisonMinDelta, 0.000_001) : comparisonMinDelta
        return Array((comparison.signalDelta ?? [:]).values)
            .filter { abs($0.delta ?? 0) >= minimum }
    }

    private func signedDelta(_ value: Double?) -> String {
        guard let value else { return "-" }
        let sign = value > 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", value))"
    }

    private var filteredRecentEvents: [ListeningEventSummary] {
        recentEvents.filter { eventPassesHistoryFilters($0) }
    }

    private var filteredPinnedEvents: [ListeningEventSummary] {
        pinnedEvents.filter { eventPassesHistoryFilters($0) }
    }

    private var allHistoryEvents: [ListeningEventSummary] {
        pinnedEvents + recentEvents
    }

    private var shownHistoryEventIds: [String] {
        uniqueIds(Array(filteredPinnedEvents.prefix(8)).map(\.id) + Array(filteredRecentEvents.prefix(8)).map(\.id))
    }

    private var historyRouteOptions: [String] {
        Array(Set(allHistoryEvents.flatMap { event in
            event.routes?.map(\.routeId) ?? []
        })).sorted()
    }

    private var historySourceOptions: [String] {
        Array(Set(allHistoryEvents.map { $0.source?.type ?? "unknown" })).sorted()
    }

    private func eventPassesHistoryFilters(_ event: ListeningEventSummary) -> Bool {
        if historyRouteFilter != "all" && event.routes?.contains(where: { $0.routeId == historyRouteFilter }) != true {
            return false
        }
        if historySourceFilter != "all" && event.source?.type != historySourceFilter {
            return false
        }
        if historyRerunnableOnly && event.segment?.dataRef?.uri?.isEmpty != false {
            return false
        }
        return true
    }

    private func isEventPinned(_ event: ListeningEventSummary) -> Bool {
        pinnedEvents.contains { $0.id == event.id }
    }

    private func uniqueIds(_ ids: [String]) -> [String] {
        var unique: [String] = []
        var seen: Set<String> = []
        for id in ids where !seen.contains(id) {
            seen.insert(id)
            unique.append(id)
        }
        return unique
    }

    private func recentMeta(_ event: ListeningEventSummary) -> String {
        let route = event.routes?.first?.routeId ?? "route"
        let source = event.source?.label ?? "local sound"
        return shortLabel("\(source) / \(route)", maxLength: 32)
    }
}
