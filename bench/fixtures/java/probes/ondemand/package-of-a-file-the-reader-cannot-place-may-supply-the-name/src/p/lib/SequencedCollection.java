package other.lib;

/**
 * A file whose directory (p/lib) is not its package's path (other/lib): ripple does not index it (its source root is not provable),
 * but javac, given the file, compiles it. So `import other.lib.*` supplies SequencedCollection (javac 21 with --release 17, where
 * java.util has none) and Callable (which java.util.* never gives: java.util.concurrent is another package). A reader that took
 * `import java.util.*` for the source of either would call a method of this file a method of the JDK. (An interface with a default
 * method: javac writes no constructor for it, so there is no invoke row in a file named by its package.)
 */
public interface SequencedCollection {
    default int size() {
        return 1;
    }
}
