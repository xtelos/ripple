package p;

import static p.Util.helper;

public class Single extends Thread {
    public void run() {
        helper();
        interrupt();
    }
}
