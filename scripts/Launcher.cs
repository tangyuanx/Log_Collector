using System;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Runtime.InteropServices;
using System.Windows.Forms;

internal static class Launcher {
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern IntPtr LoadLibraryEx(string file, IntPtr reserved, uint flags);
    [DllImport("kernel32.dll")] static extern bool FreeLibrary(IntPtr handle);
    static string Hash(byte[] data) { using (var sha=SHA256.Create()) return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "").ToLowerInvariant(); }
    static string Quote(string value) {
        var result=new StringBuilder("\""); int slashes=0;
        foreach(char c in value) {
            if(c=='\\') { slashes++; continue; }
            if(c=='\"') { result.Append('\\', slashes*2+1); result.Append(c); }
            else { result.Append('\\', slashes); result.Append(c); }
            slashes=0;
        }
        result.Append('\\',slashes*2); return result.Append('"').ToString();
    }
    [STAThread] static int Main(string[] args) {
        string local=Environment.GetEnvironmentVariable("LOCALAPPDATA");
        if(String.IsNullOrEmpty(local)) local=Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string root=Path.Combine(local,"LogCollector");
        string runtime="";
        try {
            byte[] payload;
            using(var stream=Assembly.GetExecutingAssembly().GetManifestResourceStream("runtime.zip"))
            using(var memory=new MemoryStream()) { stream.CopyTo(memory); payload=memory.ToArray(); }
            string digest=Hash(payload);
            runtime=Path.Combine(root,"runtime",digest);
            Directory.CreateDirectory(Path.GetDirectoryName(runtime));
            using(var mutex=new Mutex(false,"Local\\LogCollector-"+digest)) {
                try { if(!mutex.WaitOne(60000)) throw new IOException("另一实例仍在准备运行文件，请稍后重试。"); }
                catch(AbandonedMutexException) { }
                try {
                    string marker=Path.Combine(runtime,"complete.sha256");
                    bool ready=File.Exists(marker) && File.ReadAllText(marker)==digest &&
                        File.Exists(Path.Combine(runtime,"LogCollector.exe")) && File.Exists(Path.Combine(runtime,"_internal","python312.dll")) &&
                        File.Exists(Path.Combine(runtime,"_internal","vcruntime140.dll")) &&
                        File.Exists(Path.Combine(runtime,"_internal","vcruntime140_1.dll")) &&
                        File.Exists(Path.Combine(runtime,"_internal","PySide6","plugins","platforms","qwindows.dll"));
                    if(!ready) {
                        if(Directory.Exists(runtime)) Directory.Delete(runtime,true);
                        string staging=runtime+"-"+Guid.NewGuid().ToString("N");
                        try {
                            Directory.CreateDirectory(staging);
                            using(var memory=new MemoryStream(payload))
                            using(var zip=new ZipArchive(memory,ZipArchiveMode.Read)) {
                                foreach(var entry in zip.Entries) {
                                    string target=Path.GetFullPath(Path.Combine(staging,entry.FullName));
                                    if(!target.StartsWith(staging+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) throw new IOException("运行包路径无效");
                                    if(entry.FullName.EndsWith("/")) { Directory.CreateDirectory(target); continue; }
                                    Directory.CreateDirectory(Path.GetDirectoryName(target));
                                    entry.ExtractToFile(target);
                                }
                            }
                            File.WriteAllText(Path.Combine(staging,"complete.sha256"),digest);
                            Directory.Move(staging,runtime);
                        } finally { if(Directory.Exists(staging)) Directory.Delete(staging,true); }
                    }
                } finally { mutex.ReleaseMutex(); }
            }
            string library=Path.Combine(runtime,"_internal","python312.dll");
            IntPtr handle=LoadLibraryEx(library,IntPtr.Zero,8); // LOAD_WITH_ALTERED_SEARCH_PATH
            if(handle==IntPtr.Zero) throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error(),"无法加载 Python 运行库："+library);
            FreeLibrary(handle);
            var start=new ProcessStartInfo(Path.Combine(runtime,"LogCollector.exe"));
            start.UseShellExecute=false;
            start.WorkingDirectory=runtime;
            start.EnvironmentVariables["LOGCOLLECTOR_LAUNCHER"]=Assembly.GetExecutingAssembly().Location;
            var arguments=new StringBuilder();
            foreach(string arg in args) { if(arguments.Length>0) arguments.Append(' '); arguments.Append(Quote(arg)); }
            start.Arguments=arguments.ToString();
            using(var child=Process.Start(start)) { child.WaitForExit(); return child.ExitCode; }
        } catch(Exception ex) {
            string report=Path.Combine(root,"startup-error.txt");
            string details=DateTime.Now.ToString("s")+"\r\nWindows: "+Environment.OSVersion+"\r\n64 bit OS: "+Environment.Is64BitOperatingSystem+"\r\nRuntime: "+runtime+"\r\n"+ex;
            try { Directory.CreateDirectory(root); File.WriteAllText(report,details,Encoding.UTF8); } catch { report="（诊断文件无法写入）"; }
            if(args.Length==0 || args[0]!="--startup-check") MessageBox.Show("日志采集无法启动。\n\n"+ex.Message+"\n\n诊断文件："+report,"Log Collector · 启动诊断",MessageBoxButtons.OK,MessageBoxIcon.Error);
            return 1;
        }
    }
}
