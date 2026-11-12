package p;

import java.util.function.Function;

interface Task9 { void run(); }
class Item9 { void m() {} }
class Other9 { void m() {} }

public class K9 {
    static Other9 it;
    static Other9 x;
    static Function<Item9, Task9> F = it -> new Task9() {
        public void run() { it.m(); }
    };
    static {
        Item9 x = new Item9();
        Task9 t = new Task9() {
            public void run() { x.m(); }
        };
    }
    {
        Item9 x = new Item9();
        Task9 t = new Task9() {
            public void run() { x.m(); }
        };
    }
    Function<Item9, Task9> G = y -> new Task9() {
        public void run() { y.m(); }
    };
}
