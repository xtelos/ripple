package other.lib;

public interface Callable {
    default int call() {
        return 2;
    }
}
