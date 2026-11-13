package p;

import java.util.Comparator;

/**
 * JDK 26 gave java.lang.Process a supertype (Closeable) and a method (close()), and Comparator the default methods max and
 * min: so in Proc, close() is Outer's on JDK 25 and earlier and Process's on 26, and in ByLength, max(a, b) is Outer's on 25
 * and earlier and Comparator's on 26. A table of the JDKs up to 25 would call both Outer's: a wrong edge. See Use.java.
 */
public class Outer {
    static String max(String a, String b) { return a; }
    static void close() {}

    static class ByLength implements Comparator<String> {
        public int compare(String a, String b) { return a.length() - b.length(); }
        String longer(String a, String b) { return max(a, b); }
    }

    abstract static class Proc extends Process {
        void done() throws Exception { close(); }
    }

    static class Mine extends Process {
        public java.io.OutputStream getOutputStream() { return null; }
        public java.io.InputStream getInputStream() { return null; }
        public java.io.InputStream getErrorStream() { return null; }
        public int waitFor() { return 0; }
        public int exitValue() { return 0; }
        public void destroy() {}
        public void close() { System.out.println("Mine.close"); }
    }
}
