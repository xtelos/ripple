package p;

import static p.Util.*;

class Worker extends Thread {
    @Override
    public void run() {
        helper();
    }
}
