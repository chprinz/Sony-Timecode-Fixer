import Foundation

struct PatchResult {
    let outputURL: URL
    let log: String
}

enum PatcherError: LocalizedError {
    case missingBundledScript
    case failed(status: Int32, output: String)

    var errorDescription: String? {
        switch self {
        case .missingBundledScript:
            return "The bundled fcpxml_tc_patcher.py script could not be found."
        case let .failed(status, output):
            return "The patcher exited with status \(status).\n\n\(output)"
        }
    }
}

final class PatcherRunner {
    func run(fcpxmlURL: URL, mediaFolderURL: URL?, outputURL: URL) throws -> PatchResult {
        guard let scriptPath = Bundle.main.path(forResource: "fcpxml_tc_patcher", ofType: "py") else {
            throw PatcherError.missingBundledScript
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/env")
        process.environment = [
            "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
            "FCPXML_TC_PATCHER_FFPROBE": "/opt/homebrew/bin/ffprobe",
        ]
        var arguments = [
            "python3",
            scriptPath,
            fcpxmlURL.path,
        ]
        if let mediaFolderURL {
            arguments.append(mediaFolderURL.path)
        }
        arguments.append(contentsOf: ["--output", outputURL.path])
        process.arguments = arguments

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe

        try process.run()
        process.waitUntilExit()

        let data = pipe.fileHandleForReading.readDataToEndOfFile()
        let output = String(data: data, encoding: .utf8) ?? ""

        guard process.terminationStatus == 0 else {
            throw PatcherError.failed(status: process.terminationStatus, output: output)
        }

        return PatchResult(outputURL: outputURL, log: output)
    }
}
