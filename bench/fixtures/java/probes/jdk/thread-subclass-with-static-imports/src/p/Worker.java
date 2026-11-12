package p;

import static p.Util.*;

public class Worker extends Thread {
    public void run() {
        helper();
        start();
        getName();
    }
}
