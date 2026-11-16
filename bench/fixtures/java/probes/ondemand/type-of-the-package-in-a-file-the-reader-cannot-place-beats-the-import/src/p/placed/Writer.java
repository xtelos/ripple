package p;

/**
 * A file of package p in a directory that is not p's path: ripple does not index it, javac compiles it. Its Writer is a type of the
 * package, which comes before `import java.io.*` (JLS 6.4.1), so `Writer` in Use.java is this and not java.io.Writer. Reader is
 * java.io's. (An interface with a default method: javac writes no constructor for it, so there is no invoke row in a file named by its package.)
 */
public interface Writer {
    default void write(int c) {}
}
