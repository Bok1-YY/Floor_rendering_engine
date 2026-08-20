using System.Text.Json;
using System.Text;
using ACadSharp;
using ACadSharp.IO;

Encoding.RegisterProvider(CodePagesEncodingProvider.Instance);

if (args.Length != 2)
{
    WriteJson(new
    {
        ok = false,
        code = "invalid_arguments",
        message = "Usage: FloorEngine.ACadSharpDwgConverter <input.dwg> <output.dxf>"
    });
    return 2;
}

string inputPath = Path.GetFullPath(args[0]);
string outputPath = Path.GetFullPath(args[1]);

if (!File.Exists(inputPath))
{
    WriteJson(new { ok = false, code = "input_missing", message = "Input DWG does not exist." });
    return 3;
}

if (!string.Equals(Path.GetExtension(inputPath), ".dwg", StringComparison.OrdinalIgnoreCase) ||
    !string.Equals(Path.GetExtension(outputPath), ".dxf", StringComparison.OrdinalIgnoreCase))
{
    WriteJson(new
    {
        ok = false,
        code = "invalid_extension",
        message = "Input must be .dwg and output must be .dxf."
    });
    return 4;
}

try
{
    Directory.CreateDirectory(Path.GetDirectoryName(outputPath)!);
    CadDocument document = DwgReader.Read(inputPath);
    using (DxfWriter writer = new(outputPath, document, binary: false))
    {
        writer.Write();
    }

    FileInfo output = new(outputPath);
    if (!output.Exists || output.Length < 32)
    {
        WriteJson(new { ok = false, code = "empty_output", message = "DXF output was not created." });
        return 5;
    }

    WriteJson(new
    {
        ok = true,
        adapter = "acadsharp",
        adapter_version = typeof(CadDocument).Assembly.GetName().Version?.ToString() ?? "",
        source_version = document.Header.Version.ToString(),
        output_bytes = output.Length
    });
    return 0;
}
catch (Exception ex)
{
    WriteJson(new
    {
        ok = false,
        code = "conversion_failed",
        message = ex.Message,
        exception_type = ex.GetType().FullName ?? ex.GetType().Name
    });
    return 10;
}

static void WriteJson(object value)
{
    Console.WriteLine(JsonSerializer.Serialize(value));
}
