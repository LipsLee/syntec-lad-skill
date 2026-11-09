using System;
using System.IO;
using System.Runtime.InteropServices;

internal static class LadSyntaxCheck
{
    [DllImport("kernel32", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern IntPtr LoadLibraryW(string fileName);

    [DllImport("kernel32", SetLastError = true)]
    private static extern IntPtr GetProcAddress(IntPtr module, IntPtr ordinalOrName);

    [DllImport("kernel32", SetLastError = true)]
    private static extern bool FreeLibrary(IntPtr module);

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate int SyntaxCheckDelegate(IntPtr data);

    private delegate int IntGetter();

    private static int Main(string[] args)
    {
        if (args.Length < 1 || args.Length > 2)
        {
            Console.Error.WriteLine("usage: LadSyntaxCheck.exe <file.lad> [LadDLL.dll]");
            return 2;
        }

        string ladPath = Path.GetFullPath(args[0]);
        string dllPath = args.Length == 2
            ? Path.GetFullPath(args[1])
            : Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "LadDLL.dll");

        if (!File.Exists(ladPath))
        {
            Console.Error.WriteLine("file not found: " + ladPath);
            return 2;
        }
        if (!File.Exists(dllPath))
        {
            Console.Error.WriteLine("LadDLL.dll not found: " + dllPath);
            return 2;
        }

        byte[] data = File.ReadAllBytes(ladPath);
        if (data.Length < 2 || data[data.Length - 2] != 0 || data[data.Length - 1] != 0xFE)
        {
            Console.Error.WriteLine("invalid .lad trailer");
            return 2;
        }

        IntPtr module = IntPtr.Zero;
        IntPtr buffer = IntPtr.Zero;
        try
        {
            module = LoadLibraryW(dllPath);
            if (module == IntPtr.Zero)
            {
                throw new System.ComponentModel.Win32Exception(Marshal.GetLastWin32Error());
            }

            SyntaxCheckDelegate syntaxCheck = GetDelegate<SyntaxCheckDelegate>(module, 121);
            IntGetter errorCount = GetDelegate<IntGetter>(module, 102);
            IntGetter errorLine = GetDelegate<IntGetter>(module, 100);
            IntGetter errorType = GetDelegate<IntGetter>(module, 124);
            IntGetter getPrivateKey = GetDelegate<IntGetter>(module, 104);
            SetIntDelegate setPrivateKey = GetDelegate<SetIntDelegate>(module, 105);

            int key = getPrivateKey();
            setPrivateKey(((key + 7) * key + 5) * key + 1);

            buffer = Marshal.AllocHGlobal(data.Length);
            Marshal.Copy(data, 0, buffer, data.Length);

            int syntaxResult = syntaxCheck(buffer);
            int count = errorCount();
            int line = errorLine();
            int type = errorType();

            Console.WriteLine("syntax_ret=" + syntaxResult);
            Console.WriteLine("error_count=" + count);
            Console.WriteLine("error_line=" + line);
            Console.WriteLine("error_type=" + type);
            return syntaxResult == 1 && count == 0 ? 0 : 1;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("native validation failed: " + ex.Message);
            return 3;
        }
        finally
        {
            if (buffer != IntPtr.Zero)
            {
                Marshal.FreeHGlobal(buffer);
            }
            if (module != IntPtr.Zero)
            {
                FreeLibrary(module);
            }
        }
    }

    private static T GetDelegate<T>(IntPtr module, int ordinal) where T : class
    {
        IntPtr address = GetProcAddress(module, new IntPtr(ordinal));
        if (address == IntPtr.Zero)
        {
            throw new InvalidOperationException(
                "ordinal #" + ordinal + " was not exported by LadDLL.dll");
        }
        return (T)(object)Marshal.GetDelegateForFunctionPointer(address, typeof(T));
    }

    [UnmanagedFunctionPointer(CallingConvention.Cdecl)]
    private delegate void SetIntDelegate(int value);
}
