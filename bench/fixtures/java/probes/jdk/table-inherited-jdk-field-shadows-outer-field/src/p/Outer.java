package p;

import java.io.FilterInputStream;
import java.io.IOException;
import java.io.Writer;
import java.util.AbstractList;

/** A field a JDK supertype inherits hides the field of the same name that an enclosing class has (JLS 6.4.1, 8.2). */
public class Outer {
    static class Item {
        public String toString() {
            return "item";
        }

        int read() {
            return 1;
        }
    }

    Item lock = new Item();
    Item in = new Item();
    Item modCount = new Item();
    Item unrelated = new Item();

    static void pick(int n) {}

    static void pick(Item i) {}

    class W extends Writer {
        public void write(char[] b, int o, int l) {}

        public void flush() {}

        public void close() {}

        String f() {
            return lock.toString() + unrelated.toString();
        }
    }

    class F extends FilterInputStream {
        F() {
            super(null);
        }

        int f() throws IOException {
            return in.read();
        }
    }

    class L extends AbstractList<String> {
        public String get(int i) {
            return "";
        }

        public int size() {
            return 0;
        }

        void f() {
            pick(modCount);
        }
    }

    class Plain extends Writer {
        public void write(char[] b, int o, int l) {}

        public void flush() {}

        public void close() {}

        void f() {
            pick(modCount);
        }
    }
}
