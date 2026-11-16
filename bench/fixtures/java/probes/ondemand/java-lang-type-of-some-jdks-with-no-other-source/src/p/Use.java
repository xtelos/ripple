package p;

import java.util.*;

class Item {}

/**
 * ProcessHandle is in java.lang from JDK 9, so the table has it for some JDKs only. Nothing else can supply the name here, so javac
 * (21) says it is java.lang.ProcessHandle, and `note` is Base's. Thread is in every JDK.
 */
abstract class Base implements ProcessHandle {
    void note(Item i) {}
}

abstract class Task extends Thread {
    void note(Item i) {}
}

public class Use {
    void run(Base b, Task t, Item i, List<String> list) {
        b.note(i);
        t.note(i);
        b.pid();
        t.getName();
        list.size();
    }
}
