package p;

import static java.lang.Math.max;
import static p.Util.max;
import static p.Util.*;
import static p.Other.*;

public class Clash {
    void f() {
        max(1, 2);
        max("a", "b");
        unique();
    }
}
