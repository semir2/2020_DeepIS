import os
from multiprocessing import Pool, Queue, Process

import scipy
import utils

import matplotlib.pyplot as plt
import numpy as np

import torch
import torch.nn as nn
from .BaseTrainer import BaseTrainer
import torch.nn.functional as F

# from sklearn.metrics import f1_score, confusion_matrix, recall_score, jaccard_similarity_score, roc_curve, precision_recall_curve

class BigTrainer(BaseTrainer):
    def __init__(self, arg, G, torch_device, recon_loss, val_loss, logger):
        super(BigTrainer, self).__init__(arg, torch_device, logger)
        self.recon_loss = recon_loss
        self.val_loss=val_loss
        
        self.G = G
        self.optim = torch.optim.Adam(self.G.parameters(), lr=arg.lrG, betas=arg.beta)
            
        self.best_metric = 1.0

        self.sigmoid = nn.Sigmoid().to(self.torch_device)

        self.load()
        self.prev_epoch_loss = 0


    def save(self, epoch, filename="models"):

        if os.path.exists(self.save_path) is False:
            os.mkdir(self.save_path)
        torch.save({"model_type" : self.model_type,
                    "start_epoch" : epoch + 1,
                    "network" : self.G.state_dict(),
                    "optimizer" : self.optim.state_dict(),
                    "best_metric": self.best_metric
                    }, self.save_path + "/%s.pth.tar"%(filename))
        print("Model saved %d epoch"%(epoch))


    def load(self, filename="models.pth.tar"):
        if os.path.exists(self.save_path + "/" + filename) is True:
            print("Load %s File"%(self.save_path))            
            ckpoint = torch.load(self.save_path + "/" + filename)
            if ckpoint["model_type"] != self.model_type:
                raise ValueError("Ckpoint Model Type is %s"%(ckpoint["model_type"]))

            self.G.load_state_dict(ckpoint['network'])
            self.optim.load_state_dict(ckpoint['optimizer'])
            self.start_epoch = ckpoint['start_epoch']
            self.best_metric = ckpoint["best_metric"]
            print("Load Model Type : %s, epoch : %d"%(ckpoint["model_type"], self.start_epoch))
        else:
            print("Load Failed, not exists file")


    def interTarget(self, target, scale):

        size=[x//scale for x in target.size()[2:]]
        threshold1=0.1
        threshold2=0.
        target1=(target>threshold1).to(torch.float)-(target<-threshold1).to(torch.float)
        target1=F.interpolate(target1, size=size, mode='trilinear', align_corners=True)
        target2=torch.squeeze((target1>threshold2).to(torch.float))+2*torch.squeeze((target1<-threshold2).to(torch.float))

        return target2.to(torch.long)


    def train(self, train_loader, val_loader=None):
        print("\nStart Train")
        self.epoch=1500

        criterion_inter=nn.CrossEntropyLoss()

        for epoch in range(self.start_epoch, self.epoch):

            if epoch<50 :
                loss_W = [0.2, 0.4, 0.4]
            elif epoch<100:
                loss_W = [0.4, 0.3, 0.3]
            elif epoch<150:
                loss_W = [0.6, 0.2, 0.2]
            else:
                loss_W = [0.8, 0.1, 0.1]

            for i, (input_, target_,_) in enumerate(train_loader):
                self.G.train()
                input_, target_= input_.to(self.torch_device), target_.to(self.torch_device)

                output_, fs3, fs4 = self.G(input_)
                recon_loss = self.recon_loss(output_, target_)
                #loss3=criterion_inter(fs3, self.interTarget(target_, 4))
                #loss4=criterion_inter(fs4, self.interTarget(target_, 8))
                #total_loss=recon_loss*loss_W[0]+loss3*loss_W[1]+loss4*loss_W[2]

                total_loss=recon_loss

                self.optim.zero_grad()
                total_loss.backward()
                self.optim.step()
            
                if (i % 10) == 0:
                    self.logger.will_write("[Train] epoch:%d loss:%f"%(epoch, recon_loss))

            if val_loader is not None:            
                self.valid(epoch, val_loader)
            else:
                self.save(epoch)
        print("End Train\n")

    def _test_foward(self, input_, target_):
        input_  = input_.to(self.torch_device)
        output_, fs3, fs4 = self.G(input_)
        target_ = target_
        input_  = input_

        return input_, output_, target_

    # TODO : Metric 정하기 
    def valid(self, epoch, val_loader):
        self.G.eval()
        with torch.no_grad():
            losssum=0
            count=0;
            for i, (input_, target_, _) in enumerate(val_loader):
                if (i >= val_loader.dataset.__len__()):
                    break
                input_, target_= input_.to(self.torch_device), target_.to(self.torch_device)
                _, output_, target_ = self._test_foward(input_, target_)
                loss=self.val_loss(output_,target_)
                losssum=losssum+loss
                count=count+1

            if losssum/count < self.best_metric:
                self.best_metric = losssum/count
                self.save(epoch,"epoch[%04d]_losssum[%f]"%(epoch, losssum/count))

            if losssum/count > 0.3 :
                self.save(epoch,"EXCEPTIONepoch[%04d]_losssum[%f]"%(epoch, losssum/count))

            self.logger.write("[Val] epoch:%d losssum:%f "%(epoch, losssum/count))
                    
    # TODO: Metric, save file 정하기
    def test(self, test_loader, savedir=None):
        print("\nStart Test")
        self.G.eval()

        if savedir==None:
            savedir='/result/test'
        else:
            savedir='/result/'+savedir

        if os.path.exists(self.save_path+'/result') is False:
            os.mkdir(self.save_path + '/result')
        if os.path.exists(self.save_path+savedir) is False:
            os.mkdir(self.save_path+savedir)

        with torch.no_grad():
            for i, (input_, target_, fname) in enumerate(test_loader):

                if(i>=test_loader.dataset.__len__()):
                    break

                z = int(input_.shape[4] // 2)
                xy_size=96
                x = xy_size
                y = xy_size
                tempinput=input_[:,:,x-xy_size:x+xy_size,y-xy_size:y+xy_size,z-48:z+48]
                output_1, fs3, fs4 = self.G(tempinput)
                x = xy_size
                y = int(input_.shape[2])-xy_size
                tempinput=input_[:,:,x-xy_size:x+xy_size,y-xy_size:y+xy_size,z-48:z+48]
                output_2, fs3, fs4 = self.G(tempinput)
                x = int(input_.shape[2])-xy_size
                y = xy_size
                tempinput=input_[:,:,x-xy_size:x+xy_size,y-xy_size:y+xy_size,z-48:z+48]
                output_3, fs3, fs4 = self.G(tempinput)
                x = int(input_.shape[2])-xy_size
                y = int(input_.shape[2])-xy_size
                tempinput=input_[:,:,x-xy_size:x+xy_size,y-xy_size:y+xy_size,z-48:z+48]
                output_4, fs3, fs4 = self.G(tempinput)


                output_1=output_1 + 0.03
                output_2 = output_2 + 0.03
                output_3 = output_3 + 0.03
                output_4 = output_4 + 0.03

                data={}
                coeff_mag=1000
                data['coeff_mag']=coeff_mag
                data['input']=(torch.squeeze(input_.type(torch.FloatTensor))).numpy()
                data['input']=data['input'].astype(np.uint8)
                data['output1']=(torch.squeeze(output_1.type(torch.FloatTensor))).numpy()
                data['output1'] = (data['output1']*coeff_mag).astype(np.uint16)
                data['output2']=(torch.squeeze(output_2.type(torch.FloatTensor))).numpy()
                data['output2'] = (data['output2']*coeff_mag).astype(np.uint16)
                data['output3']=(torch.squeeze(output_3.type(torch.FloatTensor))).numpy()
                data['output3'] = (data['output3']*coeff_mag).astype(np.uint16)
                data['output4']=(torch.squeeze(output_4.type(torch.FloatTensor))).numpy()
                data['output4'] = (data['output4']*coeff_mag).astype(np.uint16)
                #scipy.io.savemat(self.save_path + savedir+"/%s.mat"%(fname[0][:-4]), data)
                scipy.io.savemat(self.save_path+savedir + "/%s.mat" % (fname[0][:-4]), data)
                self.logger.will_write("[Save] fname:%s "%(fname[0][:-4]))
        print("End Test\n")
