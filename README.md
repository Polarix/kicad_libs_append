# KiCAD 补充库

此项目用于总结和归类我个人在使用KiCAD过程中用到的、KiCAD自带库中未提供的符号和封装。这些封装是我从KiCAD 5版本开始积累的，有些封装可能现在最新的库中已经提供了，也可能是我当时没有找到而自己做的符号和封装。    

这个仓库中绝大部分的封装已经过制板测试，当然也有小部分封装做好了但是没有使用过，封装的命名和引脚排布规则尽量符合KiCAD自带库的风格标准，尽量做到不埋雷，但是个人精力有限，各人使用习惯也各有不同，使用时还请自行校验。    

## 托管

此项目目前在一下仓库中托管，同步更新。

 - Github: [Polarix/kicad_libs_append](https://github.com/Polarix/kicad_libs_append)
 - Gitee: [Polarix/kicad_libs_append](https://gitee.com/Polarix/kicad_libs_append)
 - GitCode: [Polarix/kicad_libs_append](https://gitcode.com/Polarix/kicad_libs_append)

## 项目结构

仓库中包含符号库、封装库和3D模型三部分，分别在symbols、footpins、3d_model和blocks四个文件夹中。
symbols用于存放符号库。
footpins用于存放封装库。
3d_model用于存放器件的3D模型。
blocks用于存访电路设计图块(需要KiCAD9.0及以上支持)。

## 符号库

符号库中包含以下类目和内容：

|名称|描述|
|-|-|
|Antenna|天线|
|Button_Switch|按钮、开关、摇杆、编码器等|
|Connector|端子、连接器|
|Deprecated|即将废弃的旧封装或临时封装|
|General|普通元件或未分类元件|
|Interface_IC|接口IC，例如USB、串口、网络接口等|
|MCU_SOC_FPGA|MCU等可编程器件|
|Modules|模块|
|Motor_Driver|电机驱动IC|
|Power_IC|电源IC|
|RF|射频器件|
|RTC|实时时钟|
|Screen|屏幕|
|Sensor|传感器|
|Board|电路板整板|
> ※ Deprecated 类目中的符号可能会经常变动，请谨慎使用。    
> ※ General 库用于不方便归类的普通、常见器件，可能会经常变动，请谨慎使用。    
> ※ 未在上述列表中记述的库，可能会在未来合并到现有的其他库或删除，请谨慎使用。    
> ※ Board库中用于存放电路板整板封装以及尺寸，通常用于给特定电路板适配附加配件等。    

## 封装库

封装库中包含以下类目和内容：

|名称|描述|
|-|-|
|antenna|天线|
|button_switch|按钮、开关、摇杆、编码器、手柄、摇杆和电位器等|
|connector|端子、连接器|
|deprecated|将被弃用或移动到其他库的封装|
|general|普通元件或未分类元件|
|modules|模块|
|pin_pad_jumper|独立点、焊盘、螺柱、跳线等|
|screens|屏幕|
|slot|插槽、插座|
|special|特殊封装|
|board|电路板整板|
> ※ deprecated 用于临时存储和验证，不做长期保存，会经常变动，可以参考，但不推荐使用。    
> ※ general 与符号库类似，用于不方便归类的普通、常见器件，可能会经常变动，请谨慎使用。    
> ※ special 用于保存一些非标准封装、异形封装或组合封装，使用前请验证是否符合实际需求。    
> ※ Board库中用于存放电路板整板封装以及尺寸，通常用于给特定电路板适配附加配件等。    

## 图块库

KiCAD 9.0版本起，添加了电路图块功能，方便新建原理图时添加一些常用的简单的电路图模块。
目前图块库中包含以下类目和内容：

|名称|描述|
|-|-|
|interface|接口电路库|
|motor_driver|电机驱动电路库|
|power|电源模块电路库|
> ※ 图块库中的模块电路可能不是典型应用，在使用前请确认用途以及与数据手册之间的差异。    

## 配置

config文件夹中存放了个人使用的符号库和封装库配置，这些配置，将来发布的一些使用KiCAD完成的硬件设计会使用到这里记录的符号库和封装库名称定义。

对于Windows系统，KiCAD的符号库和封装库配置保存在%APPDATA%/kicad/8.0目录下，sym-lib-table文件中记录符号库的名称和路径，fp-lib-table文件中记录封装库的名称和路径，design-block-lib-table文件中记录图块库的名称和路径。

对于Ubuntu系统，这些配置文件的路径在/home/\$USE/.config/kicad/8.0目录下。

> - %APPDATA%/kicad/8.0这个路径中，8.0指KiCAD的发布版本，不同的发布版本这个文件夹名字会有不同。
> - Ubuntu下\$USER环境变量代表的是当前用户的用户名，例如在我的环境下，KiCAD保存配置文件的路径就是/home/polarix/.config/kicad。当然，/home/\$USER这个路径在命令行中也可以用“~”符号替代，即~/.config/kicad。
> - 如果你是第一次安装并运行KiCAD那么这些配置文件可能不存在，这些文件通常是用户在进行初次配置后被创建。您可以启动KiCAD后，分别进入路径配置、符号库配置和封装库配置选项，然后不用做任何改动，确认退出就可以看到这些文件了。

库文件的记录使用S表达式(S-Exp)格式记录，若是简易使用，可以在KICAD文件中添加名为APPEND_LIB_PATH的路径配置，并指向仓库克隆的本地路径，然后直接将这两个文件中的内容按照S表达式的指定格式合并到系统中相应的文件中去。
