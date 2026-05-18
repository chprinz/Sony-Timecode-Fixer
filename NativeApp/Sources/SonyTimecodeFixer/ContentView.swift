import AppKit
import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @State private var fcpxmlURL: URL?
    @State private var mediaFolderURL: URL?
    @State private var outputURL: URL?
    @State private var logText = ""
    @State private var errorText: String?
    @State private var isRunning = false
    @State private var fcpxmlDropTargeted = false
    @State private var folderDropTargeted = false

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                header

                HStack(spacing: 14) {
                    DropTargetView(
                        title: "FCPXML",
                        subtitle: fcpxmlURL?.lastPathComponent ?? "Drop XML here",
                        systemImage: "doc.badge.gearshape",
                        isTargeted: fcpxmlDropTargeted,
                        actionTitle: "Choose",
                        action: chooseFCPXML
                    )
                    .onDrop(of: [UTType.fileURL.identifier], isTargeted: $fcpxmlDropTargeted) { providers in
                        loadDroppedURL(from: providers, mode: .fcpxml) { url in
                            fcpxmlURL = url
                            outputURL = nil
                        }
                    }

                    DropTargetView(
                        title: "MP4 Folder",
                        subtitle: mediaFolderURL?.lastPathComponent ?? "Optional fallback",
                        detail: "Used only if XML paths do not point to the original Sony files.",
                        systemImage: "folder.badge.gearshape",
                        isTargeted: folderDropTargeted,
                        actionTitle: "Choose",
                        action: chooseMediaFolder
                    )
                    .onDrop(of: [UTType.fileURL.identifier], isTargeted: $folderDropTargeted) { providers in
                        loadDroppedURL(from: providers, mode: .folder) { mediaFolderURL = $0 }
                    }
                }

                outputRow
                actionRow
                logPanel
            }
            .padding(.horizontal, 24)
            .padding(.top, 34)
            .padding(.bottom, 24)
        }
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("Sony Timecode Fixer")
                .font(.title2.weight(.semibold))
            Text("Patch Final Cut Pro XML exports for Sony XAVC-S MP4 relinking in DaVinci Resolve.")
                .foregroundStyle(.secondary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var outputRow: some View {
        HStack(spacing: 12) {
            Label(outputURL?.path(percentEncoded: false) ?? defaultOutputURL?.path(percentEncoded: false) ?? "Output file", systemImage: "square.and.arrow.down")
                .lineLimit(1)
                .truncationMode(.middle)
                .foregroundStyle(outputURL == nil && defaultOutputURL == nil ? .secondary : .primary)

            Spacer()

            Button("Save As...", systemImage: "pencil") {
                chooseOutput()
            }
            .disabled(fcpxmlURL == nil)
        }
        .padding(12)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
    }

    private var actionRow: some View {
        HStack(spacing: 10) {
            Button("Patch FCPXML", systemImage: "wand.and.stars") {
                patch()
            }
            .buttonStyle(.borderedProminent)
            .disabled(!canPatch)

            if let outputURL {
                Button("Reveal in Finder", systemImage: "finder") {
                    reveal(outputURL)
                }
            }

            Spacer()

            if isRunning {
                ProgressView()
                    .controlSize(.small)
            }
        }
    }

    private var logPanel: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(errorText == nil ? "Log" : "Error")
                .font(.headline)

            ScrollView {
                Text(errorText ?? logText.ifEmpty("No run yet."))
                    .font(.system(.body, design: .monospaced))
                    .foregroundColor(errorText == nil ? .primary : .red)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .textSelection(.enabled)
            }
            .frame(minHeight: 180)
            .padding(12)
            .background(Color(nsColor: .textBackgroundColor), in: RoundedRectangle(cornerRadius: 8))
        }
    }

    private var canPatch: Bool {
        fcpxmlURL != nil && !isRunning
    }

    private var defaultOutputURL: URL? {
        guard let fcpxmlURL else { return nil }
        let baseName = fcpxmlURL.deletingPathExtension().lastPathComponent
        let outputExtension = fcpxmlURL.pathExtension.lowercased() == "fcpxmld"
            ? "fcpxml"
            : (fcpxmlURL.pathExtension.isEmpty ? "fcpxml" : fcpxmlURL.pathExtension)
        return fcpxmlURL
            .deletingLastPathComponent()
            .appendingPathComponent("\(baseName)_tc_fixed")
            .appendingPathExtension(outputExtension)
    }

    private func chooseFCPXML() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = true
        panel.canChooseFiles = true
        panel.treatsFilePackagesAsDirectories = false
        panel.title = "Choose FCPXML"
        panel.message = "Select a .fcpxml, .fcpxmld, or .xml export from Final Cut Pro."
        panel.prompt = "Choose"

        if panel.runModal() == .OK {
            if let url = panel.url, acceptsFCPXML(url) {
                fcpxmlURL = url
                outputURL = nil
            } else if let url = panel.url {
                errorText = "Selected file is not a supported FCPXML file or package:\n\(url.path)"
            }
        }
    }

    private func chooseMediaFolder() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.treatsFilePackagesAsDirectories = true
        panel.title = "Choose Sony MP4 Folder"
        panel.message = "Select the folder containing the original Sony MP4 files."
        panel.prompt = "Choose"

        if panel.runModal() == .OK {
            mediaFolderURL = panel.url
        } else {
            errorText = nil
        }
    }

    private func chooseOutput() {
        guard let defaultOutputURL else { return }

        let panel = NSSavePanel()
        panel.nameFieldStringValue = defaultOutputURL.lastPathComponent
        panel.directoryURL = defaultOutputURL.deletingLastPathComponent()

        if panel.runModal() == .OK {
            outputURL = panel.url
        }
    }

    private func patch() {
        guard let fcpxmlURL else { return }
        let selectedMediaFolderURL = mediaFolderURL
        let destinationURL = outputURL ?? defaultOutputURL
        guard let destinationURL else { return }

        isRunning = true
        errorText = nil
        logText = ""

        Task {
            do {
                let result = try await Task.detached {
                    try PatcherRunner().run(
                        fcpxmlURL: fcpxmlURL,
                        mediaFolderURL: selectedMediaFolderURL,
                        outputURL: destinationURL
                    )
                }.value
                await MainActor.run {
                    outputURL = result.outputURL
                    logText = result.log
                    isRunning = false
                }
            } catch {
                await MainActor.run {
                    errorText = error.localizedDescription
                    isRunning = false
                }
            }
        }
    }

    private func reveal(_ url: URL) {
        NSWorkspace.shared.activateFileViewerSelecting([url])
    }

    private func loadDroppedURL(
        from providers: [NSItemProvider],
        mode: DropMode,
        assign: @escaping (URL) -> Void
    ) -> Bool {
        guard let provider = providers.first else { return false }

        provider.loadItem(forTypeIdentifier: UTType.fileURL.identifier, options: nil) { item, _ in
            let url: URL?
            if let data = item as? Data {
                url = URL(dataRepresentation: data, relativeTo: nil)
            } else if let droppedURL = item as? URL {
                url = droppedURL
            } else {
                url = nil
            }

            guard let url else { return }

            guard accepts(url, for: mode) else {
                DispatchQueue.main.async {
                    errorText = "Dropped item is not valid for \(mode.label):\n\(url.path)"
                }
                return
            }

            DispatchQueue.main.async {
                assign(url)
                errorText = nil
            }
        }

        return true
    }

    private func accepts(_ url: URL, for mode: DropMode) -> Bool {
        switch mode {
        case .fcpxml:
            acceptsFCPXML(url)
        case .folder:
            isDirectory(url)
        }
    }

    private func acceptsFCPXML(_ url: URL) -> Bool {
        let ext = url.pathExtension.lowercased()
        if ["fcpxml", "fcpxmld", "xml"].contains(ext) {
            return true
        }
        return isDirectory(url) && url.lastPathComponent.lowercased().hasSuffix(".fcpxmld")
    }

    private func isDirectory(_ url: URL) -> Bool {
        var isDirectory: ObjCBool = false
        FileManager.default.fileExists(atPath: url.path, isDirectory: &isDirectory)
        return isDirectory.boolValue
    }
}

private enum DropMode {
    case fcpxml
    case folder

    var label: String {
        switch self {
        case .fcpxml:
            return "the FCPXML drop area"
        case .folder:
            return "the Sony MP4 folder drop area"
        }
    }
}

private struct DropTargetView: View {
    let title: String
    let subtitle: String
    var detail: String? = nil
    let systemImage: String
    let isTargeted: Bool
    let actionTitle: String
    let action: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            Image(systemName: systemImage)
                .font(.system(size: 32))
                .foregroundStyle(isTargeted ? .blue : .secondary)
                .frame(width: 42, height: 42)

            VStack(alignment: .leading, spacing: 10) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(.headline)
                    Text(subtitle)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if let detail {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(.tertiary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }

                Button(actionTitle, systemImage: "plus") {
                    action()
                }
            }
        }
        .frame(maxWidth: .infinity, minHeight: 122, alignment: .topLeading)
        .padding(16)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .strokeBorder(isTargeted ? Color.blue : Color.secondary.opacity(0.25), lineWidth: isTargeted ? 2 : 1)
        }
    }
}

private extension String {
    func ifEmpty(_ fallback: String) -> String {
        isEmpty ? fallback : self
    }
}
